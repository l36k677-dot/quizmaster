"""大模型客户端（DeepSeek 的 Anthropic 兼容入口）。

只做一件事：把 system + user prompt 发出去，拿回纯文本。
超时、非 200、结构异常统一抛 LLMError，由上层降级。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core.config import settings

#: 设计文档 15.4 的 System 约束
SYSTEM_PROMPT = (
    "你是南开大学校史知识解析助手。只能依据提供的资料片段和题库标准答案进行解释。\n"
    "不得改变标准答案，不得编造来源，不得引入资料之外的史实。资料不足时必须明确说明。\n"
    "输出简洁中文，并严格返回如下 JSON（不要输出多余文字、不要使用 Markdown 代码块）：\n"
    '{"explanation": "为什么标准答案正确，以及用户答案错在哪里", '
    '"citations": [{"chunkId": "...", "documentTitle": "...", "sectionTitle": "..."}]}'
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


class LLMError(RuntimeError):
    """模型调用失败（超时、网络、协议或输出不可解析）。"""


def endpoint() -> str:
    return f"{settings.ai_base_url.rstrip('/')}/v1/messages"


def build_user_prompt(
    *,
    content: str,
    options: dict[str, str],
    correct_answer: str,
    user_answer: str | None,
    base_explanation: str,
    chunks: list[dict[str, Any]],
    correct_answer_text: str | None = None,
    user_answer_text: str | None = None,
) -> str:
    """拼装用户提示词。

    单选与判断题的答案就是键位，可以直接去 ``options`` 里取文本；
    填空题没有选项，答案本身就是文本，由调用方通过
    ``correct_answer_text`` / ``user_answer_text`` 显式传入 ——
    否则提示词里会出现「【题库标准答案】. 」这种空答案。
    """
    option_lines = "\n".join(f"{key}. {value}" for key, value in options.items())
    chunk_lines = "\n\n".join(
        f"[片段 {index + 1}] chunkId={c['chunkId']}\n"
        f"资料：{c['documentTitle']} / {c['sectionTitle']}\n"
        f"内容：{c['content']}"
        for index, c in enumerate(chunks)
    )
    if correct_answer_text is None:
        correct_answer_text = options.get(correct_answer, "")
    if user_answer_text is None:
        user_answer_text = options.get(user_answer or "", "") if user_answer else ""

    answer_line = f"{correct_answer}. {correct_answer_text}".strip().rstrip(".")
    if not options:
        # 填空题：答案本身就是句子，不要再拼键位
        answer_line = correct_answer_text or correct_answer
    shown_user = user_answer_text or "未作答"
    if user_answer and options:
        shown_user = f"{user_answer}. {user_answer_text}".strip().rstrip(".")
    return (
        f"【题目】{content}\n"
        f"【选项】\n{option_lines or '（填空题，无选项）'}\n"
        f"【题库标准答案】{answer_line}\n"
        f"【用户答案】{shown_user}\n"
        f"【题库基础解析】{base_explanation or '（无）'}\n\n"
        f"【可引用的资料片段（只能引用以下 chunkId）】\n{chunk_lines}\n\n"
        "请生成对用户的补充解释，并在 citations 中列出实际依据的片段。"
        "若资料不足以支撑解释，请把 citations 返回为空数组并在 explanation 中说明资料不足。"
    )


async def call_model(user_prompt: str) -> str:
    """调用模型并返回文本。"""
    if not settings.ai_configured:
        raise LLMError("未配置模型接入信息")

    payload = {
        "model": settings.ai_model,
        "max_tokens": 900,
        "temperature": 0.2,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    headers = {
        "x-api-key": settings.ai_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    timeout = httpx.Timeout(settings.ai_timeout_seconds, connect=min(5, settings.ai_timeout_seconds))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint(), json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMError("模型调用超时") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"模型网络异常：{exc}") from exc

    if response.status_code >= 400:
        raise LLMError(f"模型返回 {response.status_code}：{response.text[:200]}")

    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError("模型返回内容不是合法 JSON") from exc

    blocks = data.get("content") or []
    text = "".join(
        block.get("text", "") for block in blocks if block.get("type") == "text"
    ).strip()
    if not text:
        raise LLMError("模型返回内容为空")
    return text


def parse_model_output(text: str) -> dict[str, Any]:
    """从模型输出中提取 explanation 与 citations，并做结构校验。"""
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise LLMError("模型输出未包含 JSON 结构")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMError("模型输出的 JSON 无法解析") from exc

    explanation = str(data.get("explanation") or "").strip()
    if len(explanation) < 10:
        raise LLMError("模型输出解释过短")

    raw_citations = data.get("citations") or []
    citations: list[dict[str, str]] = []
    if isinstance(raw_citations, list):
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunkId") or "").strip()
            if not chunk_id:
                continue
            citations.append({
                "chunkId": chunk_id,
                "documentTitle": str(item.get("documentTitle") or "").strip(),
                "sectionTitle": str(item.get("sectionTitle") or "").strip(),
            })
    return {"explanation": explanation, "citations": citations}
