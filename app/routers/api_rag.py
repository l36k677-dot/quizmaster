"""RAG 接口：资料导入/列表/删除，以及对已提交题目的资料增强解析。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.core.response import ok
from app.models import User
from app.rag import service as rag_service
from app.routers.deps import current_admin, current_user
from app.schemas import KnowledgeIngestRequest, RagExplainRequest
from app.services import quiz_service

router = APIRouter(tags=["rag"])


@router.post("/api/admin/knowledge")
def ingest_knowledge(
    payload: KnowledgeIngestRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    document = rag_service.ingest_text(
        db,
        text=payload.content,
        title=payload.title,
        course=payload.course,
        file_name=payload.fileName,
        created_by=admin.id,
    )
    return ok({
        "id": document.id,
        "title": document.title,
        "chunkCount": document.chunk_count,
        "status": document.status,
    })


@router.get("/api/admin/knowledge")
def list_knowledge(db: Session = Depends(get_db), admin: User = Depends(current_admin)):
    return ok(rag_service.rag_status(db))


@router.delete("/api/admin/knowledge/{document_id}")
def delete_knowledge(
    document_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    rag_service.delete_document(db, document_id)
    return ok({"deleted": document_id})


@router.post("/api/rag/explanations")
async def explain(
    payload: RagExplainRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """为已提交题目生成资料增强解析。

    权限与前置条件：功能开关 → 会话归属 → 会话已结束 → 缓存 → 引用可验证。
    """
    if not settings.rag_enabled:
        raise APIError(ErrorCode.RAG_DISABLED)

    session = quiz_service.get_owned_session(db, user, payload.sessionId)
    quiz_service.ensure_finalized_if_expired(db, session)
    if not session.is_final:
        raise APIError(ErrorCode.SESSION_NOT_FOUND, "本局尚未结束，暂不能生成增强解析")

    target = next(
        (q for q in session.session_questions if q.id == payload.sessionQuestionId), None
    )
    if target is None:
        raise APIError(ErrorCode.NOT_IN_SESSION)

    user_answer = target.answer.user_answer if target.answer else None
    data = await rag_service.enhance_explanation(
        db, session=session, session_question=target, user_answer=user_answer, user=user
    )
    return ok(data)
