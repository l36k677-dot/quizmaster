"""公共依赖：登录态、角色校验、页面渲染上下文。"""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.core.security import SESSION_COOKIE_NAME, read_session_token
from app.models import Role, User
from app.services import rewards as reward_service
from app.services import tour as tour_service


def load_user(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    data = read_session_token(token)
    if not data:
        return None
    return db.get(User, data.get("uid", ""))


def current_user_optional(
    db: Session = Depends(get_db),
    qm_session: str | None = Cookie(default=None),
) -> User | None:
    return load_user(db, qm_session)


def current_user(
    user: User | None = Depends(current_user_optional),
) -> User:
    if user is None:
        raise APIError(ErrorCode.AUTH_REQUIRED)
    return user


def current_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.ADMIN:
        raise APIError(ErrorCode.FORBIDDEN)
    return user


def page_context(
    request: Request,
    user: User | None,
    db: Session | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """页面模板公共上下文（含侧栏所需的等级称号）。"""
    context: dict[str, Any] = {
        "request": request,
        "current_user": user,
        "app_name": settings.app_name,
        "app_env": settings.app_env,
        "reward_enabled": settings.reward_enabled,
        "rag_enabled": settings.rag_enabled,
        "ai_configured": settings.ai_configured,
        "per_question_seconds": settings.per_question_seconds,
        "active_nav": extra.pop("active_nav", ""),
        "flash": extra.pop("flash", None),
        "reward": None,
        "tour": None,
    }
    if user is not None and db is not None and settings.reward_enabled:
        try:
            context["reward"] = reward_service.reward_summary(db, user)
        except Exception:  # noqa: BLE001  页面渲染不因奖励故障而失败
            context["reward"] = None
    elif user is not None:
        # 奖励关闭时仍展示等级信息（纯函数，不读库）
        context["reward"] = {
            "studyPoints": user.study_points or 0,
            "level": reward_service.level_for(user.study_points or 0),
            "title": reward_service.level_for(user.study_points or 0)["title"],
        }

    # 新手指引：文案与步骤来自服务端（app/services/tour.py），前端只负责
    # 高亮与定位。只在「首页 + 没看过」时自动开场；其余页面靠账户菜单
    # 里的入口手动打开（`?tour=1` 可强制，便于演示与截图）。
    if user is not None:
        seen = bool(getattr(user, "tour_seen", False))
        active_nav = context["active_nav"]
        context["tour"] = tour_service.page_payload(
            seen=seen,
            auto_start=active_nav == "dashboard" and not seen,
        )

    context.update(extra)
    return context

