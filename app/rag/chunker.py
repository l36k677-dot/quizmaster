"""讲义切分：按 Markdown 标题与长度切块，块间保留重叠。

规则（设计文档 15.3）：
- 每块约 300～600 个中文字符，重叠 50～80 字；
- 保留章节标题作为来源引用；
- 提取关键词用于稀疏检索加权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")
HAN_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: 中文停用词（精简版，够用于讲义检索降噪）
STOPWORDS = {
    "的", "了", "和", "是", "在", "有", "与", "为", "对", "也", "就", "都", "而", "及",
    "中", "上", "下", "一个", "我们", "他们", "可以", "这个", "那个", "以及", "通过",
    "进行", "需要", "其中", "因此", "但是", "如果", "并且", "或者", "由于", "同时",
}


@dataclass
class Chunk:
    """一个知识切片。"""

    document_title: str
    section_title: str
    chunk_index: int
    content: str
    keywords: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "documentTitle": self.document_title,
            "sectionTitle": self.section_title,
            "chunkIndex": self.chunk_index,
            "content": self.content,
            "keywords": self.keywords,
        }


def extract_keywords(text: str, limit: int = 12) -> list[str]:
    """抽取关键词：英文术语 + 中文二元组高频项。"""
    counter: dict[str, int] = {}

    for word in WORD_RE.findall(text):
        token = word.lower()
        if len(token) >= 3:
            counter[token] = counter.get(token, 0) + 2

    for run in HAN_RUN_RE.findall(text):
        if len(run) < 2:
            continue
        for index in range(len(run) - 1):
            bigram = run[index:index + 2]
            if bigram in STOPWORDS:
                continue
            counter[bigram] = counter.get(bigram, 0) + 1

    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _count in ranked[:limit]]


def _split_paragraphs(body: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", body)]
    return [p for p in parts if p and not p.startswith("```")]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """把超长段落按句子边界切成不超过 max_chars 的片段。"""
    sentences = [s for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(buffer) + len(sentence) <= max_chars:
            buffer += sentence
        else:
            if buffer:
                pieces.append(buffer)
            while len(sentence) > max_chars:
                pieces.append(sentence[:max_chars])
                sentence = sentence[max_chars:]
            buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_markdown(
    text: str,
    document_title: str,
    *,
    min_chars: int = 300,
    max_chars: int = 600,
    overlap: int = 60,
) -> list[Chunk]:
    """把一份 Markdown 讲义切成有序切片。"""
    chunks: list[Chunk] = []
    section_title = document_title
    index = 0

    # 1) 先按标题切段，保留章节上下文
    sections: list[tuple[str, str]] = []
    buffer_lines: list[str] = []
    for raw_line in text.splitlines():
        match = HEADING_RE.match(raw_line)
        if match:
            if buffer_lines:
                sections.append((section_title, "\n".join(buffer_lines).strip()))
                buffer_lines = []
            section_title = match.group(2).strip()
            continue
        buffer_lines.append(raw_line)
    if buffer_lines:
        sections.append((section_title, "\n".join(buffer_lines).strip()))

    # 文档开头的引言通常很短，单独成块会产生无信息量的碎片，直接并入首个正文章节
    if len(sections) > 1 and len(sections[0][1]) < min_chars:
        _head_title, head_body = sections.pop(0)
        next_title, next_body = sections[0]
        merged = f"{head_body}\n{next_body}".strip()
        sections[0] = (next_title, merged)

    # 2) 段内按长度合块，相邻块保留 overlap 字重叠
    for title, body in sections:
        body = body.strip()
        if not body:
            continue
        pieces: list[str] = []
        current = ""
        for paragraph in _split_paragraphs(body):
            for piece in _hard_split(paragraph, max_chars):
                if not current:
                    current = piece
                elif len(current) + len(piece) + 2 <= max_chars:
                    current = f"{current}\n{piece}"
                else:
                    pieces.append(current)
                    tail = current[-overlap:] if overlap > 0 else ""
                    current = f"{tail}\n{piece}" if tail else piece
        if current:
            pieces.append(current)

        for piece in pieces:
            content = piece.strip()
            if not content:
                continue
            # 过短的尾巴并入上一块（但不能把块撑得过大，否则检索粒度会变粗）
            if len(content) < min_chars and chunks:
                last = chunks[-1]
                if len(last.content) + len(content) + 1 <= max_chars:
                    if last.section_title != title:
                        # 跨章节合并时把两个章节名都保留，引用才准确
                        last.section_title = f"{last.section_title} · {title}"
                    last.content = f"{last.content}\n{content}"
                    last.keywords = extract_keywords(last.content)
                    continue
            chunks.append(
                Chunk(
                    document_title=document_title,
                    section_title=title,
                    chunk_index=index,
                    content=content,
                    keywords=extract_keywords(content),
                )
            )
            index += 1

    return chunks
