"""RAG 编排：导入讲义 → 构建稀疏索引 → 检索 → 生成 → 缓存 → 降级。

所有失败路径都必须让结果页照常可用（设计文档 15.7）。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import CORPUS_DIR, settings
from app.core.errors import APIError, ErrorCode
from app.core.security import new_id
from app.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    QuestionType,
    QuizSession,
    RagExplanation,
    RagStatus,
    SessionQuestion,
    User,
)
from app.rag.chunker import chunk_markdown
from app.rag.llm import LLMError, build_user_prompt, call_model, parse_model_output
from app.rag.retriever import SparseIndex, build_query

MAX_UPLOAD_BYTES = 1_000_000


# ------------------------------------------------------------------ 导入
def ingest_text(
    db: Session,
    *,
    text: str,
    title: str,
    course: str | None = None,
    file_name: str | None = None,
    created_by: str | None = None,
) -> KnowledgeDocument:
    """把一份讲义正文建索引。相同内容哈希会复用已有文档。"""
    content = (text or "").strip()
    if not content:
        raise APIError(ErrorCode.VALIDATION_ERROR, "资料内容为空")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    existing = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.content_hash == digest)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    document = KnowledgeDocument(
        id=new_id("doc_"),
        title=title or (file_name or "未命名资料"),
        course=course,
        file_name=file_name,
        content_hash=digest,
        status="READY",
        created_by=created_by,
    )
    db.add(document)
    db.flush()

    chunks = chunk_markdown(content, document.title)
    for chunk in chunks:
        db.add(
            KnowledgeChunk(
                id=new_id("chk_"),
                document_id=document.id,
                section_title=chunk.section_title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                keywords_json=json.dumps(chunk.keywords, ensure_ascii=False),
            )
        )
    document.chunk_count = len(chunks)
    db.commit()
    return document


def ingest_file(db: Session, path: Path, *, created_by: str | None = None) -> KnowledgeDocument:
    if path.suffix.lower() not in {".md", ".txt", ".markdown"}:
        raise APIError(ErrorCode.VALIDATION_ERROR, "只支持 .md / .txt 资料")
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise APIError(ErrorCode.VALIDATION_ERROR, "资料超过 1MB 上限")
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 文件名去掉排序前缀（如 01_），让来源引用读起来干净
    title = re.sub(r"^\d+[_\-\s]*", "", path.stem).strip() or path.stem
    course = "南开校史"
    return ingest_text(
        db, text=text, title=title, course=course, file_name=path.name, created_by=created_by
    )


def ingest_corpus_dir(db: Session, *, created_by: str | None = None) -> list[KnowledgeDocument]:
    """把 data/corpus 下所有讲义导入（seed 使用）。"""
    documents: list[KnowledgeDocument] = []
    if not CORPUS_DIR.exists():
        return documents
    for path in sorted(CORPUS_DIR.glob("*")):
        if path.suffix.lower() in {".md", ".txt", ".markdown"}:
            documents.append(ingest_file(db, path, created_by=created_by))
    return documents


def list_documents(db: Session) -> list[dict[str, Any]]:
    rows = list(
        db.execute(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())).scalars()
    )
    return [
        {
            "id": row.id,
            "title": row.title,
            "course": row.course,
            "fileName": row.file_name,
            "status": row.status,
            "chunkCount": row.chunk_count,
            "createdAt": row.created_at.isoformat(),
        }
        for row in rows
    ]


def delete_document(db: Session, document_id: str) -> None:
    document = db.get(KnowledgeDocument, document_id)
    if document is None:
        raise APIError(ErrorCode.NOT_FOUND, "资料不存在")
    db.delete(document)
    db.commit()


# ------------------------------------------------------------------ 索引
def load_chunk_rows(db: Session) -> list[dict[str, Any]]:
    rows = list(
        db.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeDocument.status == "READY")
            .order_by(KnowledgeDocument.title, KnowledgeChunk.chunk_index)
        )
    )
    return [
        {
            "id": chunk.id,
            "documentTitle": document.title,
            "sectionTitle": chunk.section_title,
            "content": chunk.content,
            "keywords": json.loads(chunk.keywords_json or "[]"),
        }
        for chunk, document in rows
    ]


def build_index(db: Session) -> SparseIndex:
    """构建内存索引。资料规模很小（百级切片），每次重建成本可忽略。"""
    return SparseIndex(load_chunk_rows(db))


# ------------------------------------------------------------------ 生成
def _cache_key(session_question_id: str, user_answer: str | None) -> RagExplanation | None:
    return None


def get_cached(
    db: Session, session_question_id: str, user_answer: str | None
) -> RagExplanation | None:
    return db.execute(
        select(RagExplanation)
        .where(RagExplanation.session_question_id == session_question_id)
        .where(RagExplanation.user_answer == user_answer)
    ).scalar_one_or_none()


def _fallback_payload(reason: str, *, status: str = RagStatus.NO_CONTEXT,
                      base_explanation: str = "", message: str = "") -> dict[str, Any]:
    return {
        "available": False,
        "status": status,
        "explanation": "",
        "citations": [],
        "reason": reason,
        "message": message or "未检索到足够相关的课程资料，以下为题库标准解析",
        "baseExplanation": base_explanation,
        "cached": False,
    }


async def enhance_explanation(
    db: Session,
    *,
    session: QuizSession,
    session_question: SessionQuestion,
    user_answer: str | None,
    user: User,
) -> dict[str, Any]:
    """为一道已提交的题目生成资料增强解析（带缓存与全链路降级）。"""
    base_explanation = session_question.explanation_snapshot or ""

    if not settings.rag_enabled:
        return _fallback_payload(
            ErrorCode.RAG_DISABLED, status=RagStatus.FAILED,
            base_explanation=base_explanation, message="资料增强解析未启用",
        )
    if not settings.ai_configured:
        return _fallback_payload(
            ErrorCode.RAG_MODEL_ERROR, status=RagStatus.FAILED,
            base_explanation=base_explanation, message="尚未配置模型接入信息，仅显示题库标准解析",
        )
    if not session.is_final:
        raise APIError(ErrorCode.SESSION_NOT_FOUND, "本局尚未结算")

    # 1) 缓存命中直接返回，避免重复调用模型与答案漂移
    cached = get_cached(db, session_question.id, user_answer)
    if cached is not None and cached.status == RagStatus.SUCCESS:
        return {
            "available": True,
            "status": RagStatus.SUCCESS,
            "explanation": cached.explanation,
            "citations": json.loads(cached.citations_json or "[]"),
            "baseExplanation": base_explanation,
            "model": cached.model_name,
            "cached": True,
            "message": "已命中缓存",
        }

    # 2) 检索
    index = build_index(db)
    if not index.chunks:
        return _fallback_payload(
            ErrorCode.RAG_NO_DOCUMENT, base_explanation=base_explanation,
            message="尚未导入任何课程资料，仅显示题库标准解析",
        )

    qtype = session_question.qtype_snapshot or QuestionType.SINGLE
    options = session_question.option_map()
    if qtype == QuestionType.BLANK:
        # 填空题没有选项：答案与作答本身就是文本
        correct_answer_text = session_question.correct_answer_snapshot
        user_answer_text = (user_answer or "").strip()
    else:
        correct_answer_text = options.get(session_question.correct_answer_snapshot, "")
        user_answer_text = options.get((user_answer or "").strip().upper(), "")

    query = build_query(
        content=session_question.content_snapshot,
        options=options.values(),
        correct_answer_text=correct_answer_text,
        user_answer_text=user_answer_text,
        category=session_question.category_snapshot,
        explanation=base_explanation,
    )
    hits, best_score = index.search(
        query, top_k=3, category_hint=session_question.category_snapshot or ""
    )
    if not hits:
        return _fallback_payload(
            ErrorCode.RAG_NO_RELEVANT_CHUNK, base_explanation=base_explanation,
            message=f"检索相关度不足（最高分 {best_score:.2f}），以下为题库标准解析",
        )

    # 3) 生成
    prompt = build_user_prompt(
        content=session_question.content_snapshot,
        options=options,
        correct_answer=session_question.correct_answer_snapshot,
        user_answer=user_answer,
        correct_answer_text=correct_answer_text,
        user_answer_text=user_answer_text,
        base_explanation=base_explanation,
        chunks=[hit.as_dict() for hit in hits],
    )
    try:
        raw = await call_model(prompt)
        parsed = parse_model_output(raw)
    except LLMError as exc:
        return _fallback_payload(
            ErrorCode.RAG_MODEL_ERROR, status=RagStatus.FAILED,
            base_explanation=base_explanation, message=f"增强解析暂不可用（{exc}），不影响本次成绩",
        )

    # 4) 引用校验：chunkId 必须来自本次 Top-3，否则丢弃
    allowed = {hit.chunk_id: hit for hit in hits}
    citations: list[dict[str, str]] = []
    for item in parsed["citations"]:
        hit = allowed.get(item["chunkId"])
        if hit is None:
            continue
        citations.append(hit.as_citation())
    if parsed["citations"] and not citations:
        return _fallback_payload(
            ErrorCode.RAG_INVALID_OUTPUT, status=RagStatus.FAILED,
            base_explanation=base_explanation,
            message="增强解析引用了不存在的资料片段，已丢弃并降级为题库解析",
        )

    # 5) 写缓存
    try:
        db.add(
            RagExplanation(
                id=new_id("rag_"),
                session_question_id=session_question.id,
                user_answer=user_answer,
                model_name=settings.ai_model,
                explanation=parsed["explanation"],
                citations_json=json.dumps(citations, ensure_ascii=False),
                status=RagStatus.SUCCESS,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001  缓存写入失败不影响本次返回
        db.rollback()

    return {
        "available": True,
        "status": RagStatus.SUCCESS,
        "explanation": parsed["explanation"],
        "citations": citations,
        "baseExplanation": base_explanation,
        "model": settings.ai_model,
        "cached": False,
        "matchedChunks": [hit.as_dict() for hit in hits],
        "message": "已基于课程资料生成增强解析",
    }


def rag_status(db: Session) -> dict[str, Any]:
    """管理员页展示的 RAG 配置与索引状态。"""
    documents = list_documents(db)
    chunk_total = sum(doc["chunkCount"] for doc in documents)
    return {
        "enabled": settings.rag_enabled,
        "modelConfigured": settings.ai_configured,
        "model": settings.ai_model,
        "baseUrl": settings.ai_base_url,
        "documentCount": len(documents),
        "chunkCount": chunk_total,
        "documents": documents,
        "timeoutSeconds": settings.ai_timeout_seconds,
    }
