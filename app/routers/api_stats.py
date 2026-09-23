"""统计接口：Dashboard、历史成绩、排行榜。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.response import ok
from app.models import User
from app.routers.deps import current_user
from app.services import stats_service

router = APIRouter(tags=["stats"])


@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return ok(stats_service.dashboard(db, user))


@router.get("/api/history")
def history(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return ok(
        stats_service.history(db, user, page=page, page_size=pageSize, status=status)
    )


@router.get("/api/leaderboard")
def leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return ok(stats_service.leaderboard(db, user, limit=limit))
