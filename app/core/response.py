"""统一响应结构与请求 ID 上下文。

成功：{ success: true, data: {...}, requestId: "..." }
失败：{ success: false, error: { code, message }, requestId: "..." }
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(value: str) -> None:
    request_id_var.set(value)


def current_request_id() -> str:
    return request_id_var.get()


def ok(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "requestId": current_request_id()}


def fail(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"success": False, "error": error, "requestId": current_request_id()}
