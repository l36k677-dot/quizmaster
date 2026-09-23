"""奖励接口：SP/等级总览、成就墙、管理端重算。

只读；除重算外不接受任何写入，也不接受客户端传入分数、SP 或成就。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.core.response import ok
from app.models import User
from app.routers.deps import current_admin, current_user
from app.schemas import RecomputeRequest
from app.services import rewards as reward_service

router = APIRouter(tags=["rewards"])


def _disabled_payload() -> dict:
    return {
        "enabled": False,
        "studyPoints": 0,
        "level": None,
        "items": [],
        "message": "奖励机制已关闭（REWARD_ENABLED=false）",
    }


@router.get("/api/rewards/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.reward_enabled:
        return ok(_disabled_payload())
    data = reward_service.reward_summary(db, user)
    data["enabled"] = True
    data["levelTable"] = [
        {"code": code, "minPoints": low, "maxPoints": high, "title": title}
        for code, low, high, title in reward_service.LEVEL_TABLE
    ]
    return ok(data)


@router.get("/api/rewards/achievements")
def achievements(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.reward_enabled:
        return ok(_disabled_payload())
    data = reward_service.achievement_wall(db, user)
    data["enabled"] = True
    return ok(data)


@router.post("/api/admin/rewards/recompute")
def recompute(
    payload: RecomputeRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(current_admin),
):
    """按终态会话重算 SP、连续天数与成就（可审计、可修复演示数据）。"""
    if payload.all:
        targets = list(db.execute(select(User)).scalars())
    elif payload.userId:
        target = db.get(User, payload.userId)
        if target is None:
            raise APIError(ErrorCode.NOT_FOUND, "用户不存在")
        targets = [target]
    else:
        targets = [admin]

    results = []
    for target in targets:
        try:
            results.append(reward_service.recompute_user(db, target))
        except Exception as exc:  # noqa: BLE001  单个用户失败不阻断其余
            db.rollback()
            results.append({"userId": target.id, "username": target.username,
                            "error": str(exc), "code": ErrorCode.REWARD_UNAVAILABLE})
    return ok({"recomputed": len(results), "results": results})
