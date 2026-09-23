"""稀疏检索器：BM25 + 标题命中 + 短语命中加权。

不依赖向量库或本地大模型，风险低、可解释性强。
中文按“单字 + 相邻双字”切分，英文/数字按单词切分。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

HAN_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[A-Za-z0-9_\-]{2,}")

K1 = 1.5
B = 0.75

#: 低于该 BM25 分数视为“资料不足”，直接降级使用题库解析
DEFAULT_THRESHOLD = 1.2


def tokenize(text: str) -> list[str]:
    """中文单字 + 相邻双字；英文/数字单词。"""
    lowered = (text or "").lower()
    tokens: list[str] = []
    for word in WORD_RE.findall(lowered):
        tokens.append(word)
    for run in HAN_RUN_RE.findall(lowered):
        tokens.extend(list(run))
        for index in range(len(run) - 1):
            tokens.append(run[index:index + 2])
    return tokens


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_title: str
    section_title: str
    content: str
    score: float
    matched_terms: list[str]

    def as_citation(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "documentTitle": self.document_title,
            "sectionTitle": self.section_title,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.as_citation(),
            "content": self.content,
            "score": round(self.score, 4),
            "matchedTerms": self.matched_terms,
        }


class SparseIndex:
    """内存稀疏索引，随资料库构建。"""

    def __init__(self, chunks: Sequence[dict[str, Any]]) -> None:
        self.chunks = list(chunks)
        self.doc_tokens: list[Counter[str]] = []
        self.doc_len: list[int] = []
        self.df: Counter[str] = Counter()

        for chunk in self.chunks:
            # 标题与关键词参与加权，让章节主题可被检索到
            heading_text = f"{chunk.get('documentTitle', '')} {chunk.get('sectionTitle', '')}"
            tokens = tokenize(chunk.get("content", "")) + tokenize(heading_text) * 2
            counter = Counter(tokens)
            self.doc_tokens.append(counter)
            self.doc_len.append(max(1, sum(counter.values())))
            for term in counter:
                self.df[term] += 1

        self.total_docs = max(1, len(self.chunks))
        self.avg_len = (
            sum(self.doc_len) / len(self.doc_len) if self.doc_len else 1.0
        )

    # -------------------------------------------------------------- 打分
    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.total_docs - df + 0.5) / (df + 0.5))

    def _bm25(self, index: int, query_terms: Counter[str]) -> tuple[float, list[str]]:
        counter = self.doc_tokens[index]
        length = self.doc_len[index]
        score = 0.0
        matched: list[str] = []
        for term, qtf in query_terms.items():
            tf = counter.get(term, 0)
            if tf <= 0:
                continue
            matched.append(term)
            numerator = tf * (K1 + 1)
            denominator = tf + K1 * (1 - B + B * length / self.avg_len)
            score += self._idf(term) * numerator / denominator * min(qtf, 3)
        return score, matched

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        threshold: float = DEFAULT_THRESHOLD,
        category_hint: str = "",
    ) -> tuple[list[RetrievedChunk], float]:
        """返回 (命中的切片, 最高分)。最高分低于阈值时返回空列表。"""
        if not self.chunks:
            return [], 0.0

        query_terms = Counter(tokenize(query))
        if not query_terms:
            return [], 0.0

        han_phrases = [p for p in re.findall(r"[\u4e00-\u9fff]{3,}", query or "")]

        scored: list[RetrievedChunk] = []
        for index, chunk in enumerate(self.chunks):
            base, matched = self._bm25(index, query_terms)
            if base <= 0:
                continue
            heading = f"{chunk.get('documentTitle', '')}{chunk.get('sectionTitle', '')}"
            content = chunk.get("content", "")
            boost = 0.0
            # 标题命中：题干关键词出现在章节标题中，权重最高
            if any(phrase in heading for phrase in han_phrases):
                boost += 1.5
            if category_hint and category_hint in heading:
                boost += 0.6
            # 短语命中：连续 3 字以上在正文中出现
            if any(phrase in content for phrase in han_phrases):
                boost += 0.8
            scored.append(
                RetrievedChunk(
                    chunk_id=chunk["id"],
                    document_title=chunk.get("documentTitle", ""),
                    section_title=chunk.get("sectionTitle", ""),
                    content=content,
                    score=base + boost,
                    matched_terms=sorted(set(matched), key=len, reverse=True)[:8],
                )
            )

        scored.sort(key=lambda c: -c.score)
        best = scored[0].score if scored else 0.0
        if best < threshold:
            return [], best
        return scored[: max(1, top_k)], best


def build_query(
    *,
    content: str,
    options: Iterable[str],
    correct_answer_text: str,
    user_answer_text: str | None,
    category: str = "",
    explanation: str = "",
) -> str:
    """构造检索查询：题干 + 分类 + 标准答案 + 用户错误答案 + 题库解析。"""
    parts = [content, category, correct_answer_text, explanation]
    if user_answer_text:
        parts.append(user_answer_text)
    parts.extend(options)
    return " ".join(p for p in parts if p)
