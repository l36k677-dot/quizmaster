"""页面路由：服务端渲染（Jinja2），保证不依赖 JS 也能看到真实数据。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.clock import format_datetime_cst, format_duration
from app.core.config import TEMPLATE_DIR, settings
from app.core.db import get_db
from app.core.errors import APIError, ErrorCode
from app.models import Role, User
from app.rag import service as rag_service
from app.routers.deps import current_user_optional, load_user, page_context
from app.services import (
    knowledge_service,
    question_service,
    quiz_service,
    rewards as reward_service,
    stats_service,
)
from app.services.scoring import DIFFICULTY_LABELS, GRADE_LABELS

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["duration"] = format_duration
templates.env.filters["datetime_cst"] = format_datetime_cst
# 同时注册为 filter 与 global：模板中两种写法都能用
templates.env.filters["grade_label"] = lambda g: GRADE_LABELS.get(g or "", "-")
templates.env.filters["difficulty_label"] = lambda d: DIFFICULTY_LABELS.get(d or "", "-")
templates.env.globals["grade_label"] = templates.env.filters["grade_label"]
templates.env.globals["difficulty_label"] = templates.env.filters["difficulty_label"]


def _render(name: str, context: dict):
    """统一渲染入口。

    Starlette 1.6 起 TemplateResponse 的首个位置参数是 request，
    这里包一层，避免每个路由都重复写 request。
    """
    return templates.TemplateResponse(context["request"], name, context)


def _login_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)


def _require_page_user(request: Request, token: str | None, db: Session) -> User | RedirectResponse:
    user = load_user(db, token)
    if user is None:
        return _login_redirect(request)
    return user


@router.get("/", response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    from fastapi import Cookie

    token = request.cookies.get("qm_session")
    user = load_user(db, token)
    return RedirectResponse(url="/dashboard" if user else "/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard", db: Session = Depends(get_db)):
    user = current_user_optional(db=db, qm_session=request.cookies.get("qm_session"))
    if user is not None:
        return RedirectResponse(url="/dashboard", status_code=303)
    return _render(
        "login.html",
        page_context(request, None, next_url=next, active_nav="login"),
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    data = stats_service.dashboard(db, user)
    categories = question_service.list_categories(db)
    category_items = [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "available": question_service.available_count(db, c.id, None),
        }
        for c in categories
    ]
    total_available = question_service.available_count(db, None, None)
    return _render(
        "dashboard.html",
        page_context(
            request, user, db,
            active_nav="dashboard",
            data=data,
            categories=category_items,
            total_available=total_available,
            allowed_counts=list(quiz_service.ALLOWED_COUNTS),
        ),
    )


@router.get("/quiz/start/{session_id}", response_class=HTMLResponse)
def quiz_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    session = quiz_service.get_owned_session(db, user, session_id)
    quiz_service.ensure_finalized_if_expired(db, session)
    if session.is_final:
        return RedirectResponse(url=f"/result/{session.id}", status_code=303)
    view = quiz_service.session_view(db, session)
    return _render(
        "quiz.html",
        page_context(request, user, db, active_nav="dashboard", session=view),
    )


@router.get("/result/{session_id}", response_class=HTMLResponse)
def result_page(session_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    session = quiz_service.get_owned_session(db, user, session_id)
    quiz_service.ensure_finalized_if_expired(db, session)
    if not session.is_final:
        return RedirectResponse(url=f"/quiz/start/{session.id}", status_code=303)
    view = quiz_service.result_view(db, session)
    return _render(
        "result.html",
        page_context(
            request, user, db,
            active_nav="dashboard",
            result=view,
            rag_available=settings.rag_available,
            achievement_meta=reward_service.achievement_meta(db),
        ),
    )


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(
    request: Request,
    reveal: int = Query(default=0, ge=0, le=1),
    db: Session = Depends(get_db),
):
    """知识库：把校史与题库知识点整理成可浏览的展馆。

    ``?reveal=1`` 让卡片以「已翻开」的状态渲染，便于教师现场直接展示答案。
    """
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    return _render(
        "knowledge.html",
        page_context(
            request, user, db,
            active_nav="knowledge",
            data=knowledge_service.overview(db),
            reveal_all=bool(reveal),
        ),
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    data = stats_service.history(db, user, page=page, page_size=10, status=status)
    return _render(
        "history.html",
        page_context(request, user, db, active_nav="history", data=data, status_filter=status),
    )


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    data = stats_service.leaderboard(db, user, limit=20)
    return _render(
        "leaderboard.html",
        page_context(request, user, db, active_nav="leaderboard", data=data),
    )


@router.get("/achievements", response_class=HTMLResponse)
def achievements_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    if not settings.reward_enabled:
        return RedirectResponse(url="/dashboard", status_code=303)
    wall = reward_service.achievement_wall(db, user)
    summary = reward_service.reward_summary(db, user)
    return _render(
        "achievements.html",
        page_context(
            request, user, db,
            active_nav="achievements",
            wall=wall,
            summary=summary,
            achievement_meta=reward_service.achievement_meta(db),
        ),
    )


@router.get("/admin/questions", response_class=HTMLResponse)
def admin_questions_page(
    request: Request,
    page: int = Query(default=1, ge=1),
    keyword: str = Query(default=""),
    categoryId: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    qtype: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != Role.ADMIN:
        raise APIError(ErrorCode.FORBIDDEN)
    listing = question_service.admin_list_questions(
        db, page=page, page_size=10, keyword=keyword,
        category_id=categoryId, difficulty=difficulty, qtype=qtype,
    )
    return _render(
        "admin_questions.html",
        page_context(
            request, user, db,
            active_nav="admin",
            listing=listing,
            stats=question_service.question_stats(db),
            categories=[{"id": c.id, "name": c.name} for c in question_service.list_categories(db, only_active=False)],
            filters={
                "keyword": keyword,
                "categoryId": categoryId or "",
                "difficulty": difficulty or "",
                "qtype": qtype or "",
            },
        ),
    )


@router.get("/admin/knowledge", response_class=HTMLResponse)
def admin_knowledge_page(request: Request, db: Session = Depends(get_db)):
    user = _require_page_user(request, request.cookies.get("qm_session"), db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != Role.ADMIN:
        raise APIError(ErrorCode.FORBIDDEN)
    return _render(
        "admin_knowledge.html",
        page_context(
            request, user, db,
            active_nav="admin_knowledge",
            status=rag_service.rag_status(db),
        ),
    )
