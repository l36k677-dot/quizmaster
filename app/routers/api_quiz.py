"""竞答接口：分类、可用题量、创建会话、保存答案、提交、结果。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.response import ok
from app.models import User
from app.routers.deps import current_user
from app.schemas import CreateSessionRequest, SaveAnswerRequest
from app.services import question_service, quiz_service

router = APIRouter(tags=["quiz"])


@router.get("/api/categories")
def categories(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = question_service.list_categories(db)
    data = []
    for row in rows:
        data.append({
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "available": question_service.available_count(db, row.id, None),
        })
    return ok({"items": data})


@router.get("/api/quiz/availability")
def availability(
    categoryId: str | None = None,
    difficulty: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    question_service.validate_filters(db, categoryId, difficulty or None)
    count = question_service.available_count(db, categoryId, difficulty or None)
    return ok({
        "available": count,
        "categoryId": categoryId,
        "difficulty": difficulty or None,
        "allowedCounts": list(quiz_service.ALLOWED_COUNTS),
        "perQuestionSeconds": quiz_service.per_question_seconds(),
    })


@router.post("/api/quiz/sessions")
def create_session(
    payload: CreateSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = quiz_service.create_session(
        db,
        user,
        question_count=payload.questionCount,
        category_id=payload.categoryId or None,
        difficulty=payload.difficulty,
    )
    return ok(quiz_service.session_view(db, session))


@router.get("/api/quiz/sessions/{session_id}")
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = quiz_service.get_owned_session(db, user, session_id)
    quiz_service.ensure_finalized_if_expired(db, session)   # 惰性超时结算
    return ok(quiz_service.session_view(db, session))


@router.put("/api/quiz/sessions/{session_id}/answers/{session_question_id}")
def save_answer(
    session_id: str,
    session_question_id: str,
    payload: SaveAnswerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return ok(
        quiz_service.save_answer(db, user, session_id, session_question_id, payload.answer)
    )


@router.post("/api/quiz/sessions/{session_id}/submit")
def submit(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return ok(quiz_service.submit_session(db, user, session_id))


@router.get("/api/quiz/sessions/{session_id}/result")
def result(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = quiz_service.get_owned_session(db, user, session_id)
    quiz_service.ensure_finalized_if_expired(db, session)
    payload = quiz_service.result_view(db, session)
    payload["reward"] = {
        "enabled": True,
        "spEarned": session.sp_earned,
        "newlyUnlocked": [],   # 新解锁只在提交响应中提示一次
    }
    return ok(payload)
