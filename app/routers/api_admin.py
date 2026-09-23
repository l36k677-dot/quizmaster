"""管理端接口：题库 CRUD 与统计。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.core.response import ok
from app.models import User
from app.routers.deps import current_admin
from app.schemas import ActiveToggleRequest, QuestionUpsertRequest
from app.services import question_service, stats_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), admin: User = Depends(current_admin)):
    data = stats_service.admin_overview(db)
    data["questionStats"] = question_service.question_stats(db)
    return ok(data)


@router.get("/questions")
def list_questions(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50),
    keyword: str = Query(default=""),
    categoryId: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    qtype: str | None = Query(default=None),
    activeOnly: bool = Query(default=False),
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    return ok(
        question_service.admin_list_questions(
            db,
            page=page,
            page_size=pageSize,
            keyword=keyword,
            category_id=categoryId,
            difficulty=difficulty,
            qtype=qtype,
            active_only=activeOnly,
        )
    )


@router.post("/questions")
def create_question(
    payload: QuestionUpsertRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    body = payload.model_dump()
    question = question_service.create_question(db, admin, body)
    names = question_service.category_map(db)
    return ok(question_service.serialize_question(question, names.get(question.category_id, "-")))


@router.get("/questions/stats")
def question_stats(db: Session = Depends(get_db), admin: User = Depends(current_admin)):
    return ok(question_service.question_stats(db))


@router.get("/questions/{question_id}")
def get_question(
    question_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    from app.models import Question

    question = db.get(Question, question_id)
    if question is None:
        raise APIError(ErrorCode.NOT_FOUND, "题目不存在")
    names = question_service.category_map(db)
    return ok(question_service.serialize_question(question, names.get(question.category_id, "-")))


@router.put("/questions/{question_id}")
def update_question(
    question_id: str,
    payload: QuestionUpsertRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    question = question_service.update_question(db, question_id, payload.model_dump())
    names = question_service.category_map(db)
    return ok(question_service.serialize_question(question, names.get(question.category_id, "-")))


@router.patch("/questions/{question_id}/active")
def toggle_question(
    question_id: str,
    payload: ActiveToggleRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    question = question_service.set_question_active(db, question_id, payload.isActive)
    names = question_service.category_map(db)
    return ok(question_service.serialize_question(question, names.get(question.category_id, "-")))


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    """软删除：停用题目而不物理删除。

    历史会话使用题目快照，因此停用不会改变任何既有成绩。
    """
    question = question_service.set_question_active(db, question_id, False)
    return ok({"id": question.id, "isActive": question.is_active, "softDeleted": True})
