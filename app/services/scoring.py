"""判分与得分口径（设计文档 8.6 / 10.2 / 10.3）。

两条铁律：
1. 分数只能由服务端计算，且由题库快照决定。
2. 用时不计入分数；超时不额外扣分。要改变这两条必须新增 rule_version。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.models import Difficulty, Grade, QuestionType

#: 口径版本常量
RULE_VERSION_ACCURACY = "A-1"   # 正确率口径（默认启用）
RULE_VERSION_WEIGHTED = "B-1"   # 难度加权口径（默认关闭）

#: 难度权重（仅口径 B 使用）
DIFFICULTY_WEIGHTS: dict[str, float] = {
    Difficulty.EASY: 1.0,
    Difficulty.MEDIUM: 1.5,
    Difficulty.HARD: 2.0,
}

#: 评级区间（10.3）：仅用于展示，不参与任何排序
GRADE_RULES: list[tuple[str, int, int]] = [
    (Grade.EXCELLENT, 100, 100),
    (Grade.GOOD, 85, 99),
    (Grade.PASS, 60, 84),
    (Grade.NEEDS_WORK, 0, 59),
]

GRADE_LABELS: dict[str, str] = {
    Grade.EXCELLENT: "卓越",
    Grade.GOOD: "优秀",
    Grade.PASS: "合格",
    Grade.NEEDS_WORK: "待提升",
}

DIFFICULTY_LABELS: dict[str, str] = {
    Difficulty.EASY: "简单",
    Difficulty.MEDIUM: "中等",
    Difficulty.HARD: "困难",
}

QUESTION_TYPE_LABELS: dict[str, str] = {
    QuestionType.SINGLE: "单选题",
    QuestionType.JUDGE: "判断题",
    QuestionType.BLANK: "填空题",
}

# ==================================================================
# 填空题判分：宽松，但不许乱来
# ==================================================================
#: 口径：**答出关键字 且 不是特别离谱 且 没有污言秽语** → 算对。
#: 这是刻意宽松的规则 —— 填空题考的是「有没有记住这个知识点」，
#: 不是「措辞是否和标准答案一字不差」。

#: 填空题答案的最大长度。超过这个长度基本可以判定是复制粘贴或灌水。
BLANK_MAX_LEN = 60

#: 至少命中 N 个关键字才算答出要点。取 1 是刻意的宽松：
#: 例如校训题的关键字是「允公允能」「日新月异」，答出任意一个即算对。
KEYWORD_HIT_MIN = 1

#: 污言秽语黑名单。命中即判错，与是否答出关键字无关。
#: 表刻意做得短而明确 —— 长名单既容易误伤，也需要持续维护。
PROFANITY_WORDS: tuple[str, ...] = (
    "傻逼", "傻比", "煞笔", "沙比", "蠢货", "脑残", "智障", "弱智", "废物",
    "垃圾东西", "去死", "滚蛋", "王八", "杂种", "贱人", "sb", "nmsl", "fuck",
    "shit", "bitch", "dick", "asshole",
)

#: 单个字符连续重复这么多次，视为「特别离谱」的乱输入（如「啊啊啊啊啊」）
_REPEAT_RUN = 5

#: 中英文数字以外的可见字符
_PUNCT_RE = re.compile(r"[\s\u3000\-_.,;:!?'\"“”‘’()（）\[\]【】{}《》<>/\\|~`^*+=@#$%&·、，。；：！？…—]")
#: 单字符连续重复
_REPEAT_RE = re.compile(r"(.)\1{%d,}" % (_REPEAT_RUN - 1))
#: 至少一个中文或字母数字
_MEANINGFUL_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")

#: 中文数字 → 阿拉伯数字（逐字映射）。
#: 年份题的答案几乎都用阿拉伯数字写，但用户很自然会敲「一九一九年」；
#: 归一化两侧都做同样处理，所以关键字「1919」能匹配上「一九一九」，
#: 「第一章」也会被同样折叠成「第1章」—— 两边一致，不会产生假通过。
_CN_DIGITS = str.maketrans("〇零一二三四五六七八九", "00123456789")


def normalize_text(text: str | None) -> str:
    """判分用的归一化：全角转半角、中文数字转阿拉伯、去空白与标点、小写化。

    归一化只用于**比较**，绝不会回写用户原文 —— 结果页展示的是用户
    真正敲进去的那串字。
    """
    if not text:
        return ""
    # NFKC 顺带处理全角字母数字（"Ａ" → "A"）
    folded = unicodedata.normalize("NFKC", str(text))
    folded = folded.translate(_CN_DIGITS)
    folded = _PUNCT_RE.sub("", folded)
    return folded.lower()


def has_profanity(text: str | None) -> bool:
    """是否含污言秽语（标点与大小写不构成绕过手段）。"""
    if not text:
        return False
    probe = normalize_text(text)
    return any(word in probe for word in PROFANITY_WORDS)


def absurd_reason(text: str | None) -> str:
    """「特别离谱」的判定，返回原因；正常则返回空串。

    三类可判定的离谱：整段复制粘贴、单字灌水、以及没有任何可识别内容。
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if len(raw) > BLANK_MAX_LEN:
        return f"答案过长（超过 {BLANK_MAX_LEN} 字），疑似直接粘贴"
    if _REPEAT_RE.search(raw):
        return "答案像是同一个字重复输入"
    if not _MEANINGFUL_RE.search(raw):
        return "答案里没有可识别的内容"
    return ""


def hit_keywords(user_answer: str | None, keywords: Iterable[str]) -> list[str]:
    """返回用户答案里命中的关键字（按关键字原样返回，便于展示）。"""
    probe = normalize_text(user_answer)
    if not probe:
        return []
    hits: list[str] = []
    for word in keywords:
        fraction = normalize_text(word)
        if fraction and fraction in probe:
            hits.append(str(word))
    return hits


def judge_blank(
    user_answer: str | None,
    keywords: Iterable[str],
    *,
    reference: str | None = None,
) -> tuple[bool, str]:
    """填空题判分（纯函数）。

    判定顺序：未作答 → 污言秽语 → 特别离谱 → 命中关键字。

    ``reference`` 是参考答案原文，作为兜底：用户写出与参考答案完全一致的
    内容（归一化后相同）也算对，避免出题人忘了配关键字时整题判不动。
    """
    raw = (user_answer or "").strip()
    if not raw:
        return False, "未作答"
    if has_profanity(raw):
        return False, "含不当用语"
    reason = absurd_reason(raw)
    if reason:
        return False, reason

    words = [str(w).strip() for w in keywords if str(w).strip()]
    hits = hit_keywords(raw, words)
    if len(hits) >= KEYWORD_HIT_MIN:
        return True, "命中关键字：" + "、".join(hits)

    if reference and normalize_text(raw) == normalize_text(reference):
        return True, "与参考答案一致"

    expect = "、".join(words) if words else "（未配置关键字）"
    return False, f"未答出要点，参考答案：{expect}"


def is_answered(qtype: str, user_answer: str | None) -> bool:
    """这道题用户是否真的作答了（空白串不算）。

    合法的键位按题型区分：判断题只认 A/B，单选认 A–D。历史数据里若残留
    了越界的键位（例如某道判断题被写进了 "C"），这里判定为「未作答」，
    而不是「作答了但答错」—— 免得老数据在结果页被算成一笔糊涂账。
    """
    if user_answer is None:
        return False
    value = str(user_answer).strip()
    if not value:
        return False
    if qtype == QuestionType.BLANK:
        return True
    if qtype == QuestionType.JUDGE:
        return value.upper() in {"A", "B"}
    return value in {"A", "B", "C", "D"}


def answer_is_correct(
    qtype: str,
    correct_answer: str,
    keywords: Iterable[str],
    user_answer: str | None,
) -> bool:
    """按题型分派判分。单选与判断题共用键位比较。"""
    if qtype == QuestionType.BLANK:
        ok, _ = judge_blank(user_answer, keywords, reference=correct_answer)
        return ok
    value = (user_answer or "").strip().upper()
    if value not in {"A", "B", "C", "D"}:
        return False
    return value == (correct_answer or "").strip().upper()


@dataclass
class QuestionResult:
    """单题判分输入。"""

    order_no: int
    difficulty: str
    correct_answer: str
    user_answer: str | None = None
    category: str = ""
    qtype: str = QuestionType.SINGLE
    keywords: tuple[str, ...] = ()


@dataclass
class ScoreResult:
    """判分输出，可直接落库或序列化。"""

    score: int
    correct_count: int
    wrong_count: int
    unanswered_count: int
    grade: str
    rule_version: str
    scoring_mode: str
    breakdown: dict[str, Any] = field(default_factory=dict)


def grade_for(score: int) -> str:
    """score -> 评级编码。"""
    for code, low, high in GRADE_RULES:
        if low <= score <= high:
            return code
    return Grade.NEEDS_WORK


def grade_label(grade: str | None) -> str:
    return GRADE_LABELS.get(grade or "", "-")


def score_for(correct_count: int, question_count: int, mode: str = "ACCURACY",
              earned_sum: float = 0.0, weight_sum: float = 0.0) -> int:
    """核心计分函数（纯函数，方便单测与纸笔验算）。"""
    if question_count <= 0:
        return 0
    if mode == "WEIGHTED" and weight_sum > 0:
        return max(0, min(100, round(earned_sum * 100 / weight_sum)))
    return max(0, min(100, round(correct_count * 100 / question_count)))


def evaluate(results: Iterable[QuestionResult], *, mode: str = "ACCURACY",
             rule_version: str = RULE_VERSION_ACCURACY) -> ScoreResult:
    """对一局逐题结果判分，并生成可复核的得分拆解。

    未作答按错误处理：计入分母，不计入分子。
    """
    items = list(results)
    question_count = len(items)
    per_question: list[dict[str, Any]] = []
    correct_count = 0
    unanswered = 0
    earned_sum = 0.0
    weight_sum = 0.0

    for item in sorted(items, key=lambda r: r.order_no):
        weight = DIFFICULTY_WEIGHTS.get(item.difficulty, 1.0)
        if mode != "WEIGHTED":
            weight = 1.0
        qtype = item.qtype or QuestionType.SINGLE
        answered = item.user_answer if is_answered(qtype, item.user_answer) else None
        is_correct = answered is not None and answer_is_correct(
            qtype, item.correct_answer, item.keywords, answered
        )
        if answered is None:
            unanswered += 1
        if is_correct:
            correct_count += 1
            earned_sum += weight
        weight_sum += weight
        per_question.append({
            "orderNo": item.order_no,
            "category": item.category,
            "difficulty": item.difficulty,
            "qtype": qtype,
            "answered": answered,
            "correct": item.correct_answer,
            "isCorrect": is_correct,
            "weight": weight,
            "earned": weight if is_correct else 0,
        })

    score = score_for(correct_count, question_count, mode, earned_sum, weight_sum)
    wrong_count = question_count - correct_count

    breakdown = {
        "ruleVersion": rule_version,
        "scoringMode": mode,
        "questionCount": question_count,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "unansweredCount": unanswered,
        "weightSum": weight_sum,
        "earnedSum": earned_sum,
        "perQuestion": per_question,
    }
    return ScoreResult(
        score=score,
        correct_count=correct_count,
        wrong_count=wrong_count,
        unanswered_count=unanswered,
        grade=grade_for(score),
        rule_version=rule_version,
        scoring_mode=mode,
        breakdown=breakdown,
    )


def render_explain(score: int, correct: int, total: int, mode: str = "ACCURACY") -> str:
    """结果页用的一句可核验算式。"""
    if mode == "WEIGHTED":
        return f"加权口径：Σ加权得分 × 100 ÷ Σ加权满分 = {score}"
    return f"正确率口径：{correct} × 100 ÷ {total} = {score}"
