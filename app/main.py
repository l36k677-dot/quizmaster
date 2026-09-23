"""FastAPI 应用装配：中间件、异常处理、静态资源、路由注册。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import STATIC_DIR, settings
from app.core.db import init_db
from app.core.errors import APIError, ErrorCode, ERROR_META
from app.core.response import current_request_id, fail, new_request_id, set_request_id
from app.routers import (
    api_admin,
    api_auth,
    api_quiz,
    api_rag,
    api_rewards,
    api_stats,
    api_tour,
    pages,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("quizmaster")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info(
        "QuizMaster 启动 | env=%s | reward=%s | rag=%s | aiConfigured=%s | 每题%ss",
        settings.app_env,
        settings.reward_enabled,
        settings.rag_enabled,
        settings.ai_configured,
        settings.per_question_seconds,
    )
    yield


app = FastAPI(
    title="QuizMaster · 南开校史在线知识竞答系统",
    description="第 17 题实现：题库管理、随机抽题、服务端计时判分、历史与排行榜、得分奖励与轻量 RAG。",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ------------------------------------------------------------------ 中间件
@app.middleware("http")
async def request_context(request: Request, call_next):
    set_request_id(new_request_id())
    response = await call_next(request)
    response.headers["X-Request-Id"] = current_request_id()
    return response


# ------------------------------------------------------------------ 异常处理
@app.exception_handler(APIError)
async def api_error_handler(_request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(exc.code, exc.message, exc.detail),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    first = (exc.errors() or [{}])[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "请求参数不合法")
    detail = f"{location}: {message}" if location else message
    return JSONResponse(
        status_code=ERROR_META[ErrorCode.VALIDATION_ERROR][0],
        content=fail(ErrorCode.VALIDATION_ERROR, detail),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception):
    logger.exception("未处理异常：%s", exc)
    return JSONResponse(
        status_code=ERROR_META[ErrorCode.INTERNAL_ERROR][0],
        content=fail(ErrorCode.INTERNAL_ERROR, ERROR_META[ErrorCode.INTERNAL_ERROR][1]),
    )


# ------------------------------------------------------------------ 路由
app.include_router(api_auth.router)
app.include_router(api_quiz.router)
app.include_router(api_stats.router)
app.include_router(api_rewards.router)
app.include_router(api_rag.router)
app.include_router(api_admin.router)
app.include_router(api_tour.router)
app.include_router(pages.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    from app.core.response import ok

    return ok({
        "status": "ok",
        "env": settings.app_env,
        "rewardEnabled": settings.reward_enabled,
        "ragEnabled": settings.rag_enabled,
        "aiConfigured": settings.ai_configured,
    })
