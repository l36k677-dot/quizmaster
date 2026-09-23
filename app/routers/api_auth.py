"""认证接口：注册、登录、登出、当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.core.response import ok
from app.core.security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    hash_password,
    issue_session_token,
    new_id,
    verify_password,
)
from app.models import Role, User
from app.routers.deps import current_user, current_user_optional
from app.schemas import LoginRequest, RegisterRequest
from app.services import rewards as reward_service

router = APIRouter(tags=["auth"])


def _user_payload(user: User) -> dict:
    payload = {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name or user.username,
        "role": user.role,
    }
    if settings.reward_enabled:
        level = reward_service.level_for(user.study_points or 0)
        payload["studyPoints"] = user.study_points or 0
        payload["level"] = level["code"]
        payload["title"] = level["title"]
    return payload


def _set_session_cookie(response: Response, user: User) -> None:
    token = issue_session_token(user.id, user.role)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,               # 禁止 JS 读取，降低 XSS 风险
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


@router.post("/api/auth/register")
def register(
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()
    exists = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if exists is not None:
        raise APIError(ErrorCode.USERNAME_TAKEN)

    user = User(
        id=new_id("u_"),
        username=username,
        display_name=(payload.displayName or username).strip(),
        password_hash=hash_password(payload.password),
        role=Role.STUDENT,           # 角色由服务端决定，不接受前端传入
        study_points=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _set_session_cookie(response, user)
    return ok(_user_payload(user))


@router.post("/api/auth/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 不区分“用户不存在”与“密码错误”，避免账号枚举
        raise APIError(ErrorCode.INVALID_CREDENTIALS)
    _set_session_cookie(response, user)
    return ok(_user_payload(user))


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return ok({"loggedOut": True})


@router.get("/api/auth/me")
def me(user: User | None = Depends(current_user_optional)):
    if user is None:
        raise APIError(ErrorCode.AUTH_REQUIRED)
    return ok(_user_payload(user))
