"""统一业务错误码与异常类型。

所有对外错误都通过 APIError 抛出，由全局异常处理器包装成
{ "success": false, "error": {...}, "requestId": "..." } 的固定结构，
保证前端只需处理一种失败形态。
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """业务错误码常量（与设计文档 16.1 一致）。"""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    FORBIDDEN = "FORBIDDEN"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"

    QUESTION_NOT_ENOUGH = "QUESTION_NOT_ENOUGH"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_FINISHED = "SESSION_FINISHED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    INVALID_OPTION = "INVALID_OPTION"
    NOT_IN_SESSION = "NOT_IN_SESSION"

    RAG_DISABLED = "RAG_DISABLED"
    RAG_NO_DOCUMENT = "RAG_NO_DOCUMENT"
    RAG_NO_RELEVANT_CHUNK = "RAG_NO_RELEVANT_CHUNK"
    RAG_MODEL_ERROR = "RAG_MODEL_ERROR"
    RAG_INVALID_OUTPUT = "RAG_INVALID_OUTPUT"

    REWARD_UNAVAILABLE = "REWARD_UNAVAILABLE"

    INTERNAL_ERROR = "INTERNAL_ERROR"


#: 错误码 -> (HTTP 状态码, 默认用户提示)
ERROR_META: dict[str, tuple[int, str]] = {
    ErrorCode.AUTH_REQUIRED: (401, "请先登录"),
    ErrorCode.INVALID_CREDENTIALS: (401, "用户名或密码不正确"),
    ErrorCode.FORBIDDEN: (403, "无权执行此操作"),
    ErrorCode.USERNAME_TAKEN: (409, "该用户名已被占用"),
    ErrorCode.VALIDATION_ERROR: (400, "请求参数不合法"),
    ErrorCode.NOT_FOUND: (404, "资源不存在"),
    ErrorCode.QUESTION_NOT_ENOUGH: (400, "当前条件下可用题目数量不足"),
    ErrorCode.SESSION_NOT_FOUND: (404, "答题会话不存在"),
    ErrorCode.SESSION_FINISHED: (409, "本局已结束，不能继续作答"),
    ErrorCode.SESSION_EXPIRED: (409, "本局已超时"),
    ErrorCode.INVALID_OPTION: (400, "选项只能是 A/B/C/D"),
    ErrorCode.NOT_IN_SESSION: (400, "该题目不属于本局"),
    ErrorCode.RAG_DISABLED: (409, "资料增强解析未启用"),
    ErrorCode.RAG_NO_DOCUMENT: (409, "尚未导入任何课程资料"),
    ErrorCode.RAG_NO_RELEVANT_CHUNK: (404, "未检索到足够相关的课程资料"),
    ErrorCode.RAG_MODEL_ERROR: (502, "增强解析暂不可用，不影响本次成绩"),
    ErrorCode.RAG_INVALID_OUTPUT: (502, "增强解析输出不可靠，已丢弃"),
    ErrorCode.REWARD_UNAVAILABLE: (409, "奖励结算暂不可用"),
    ErrorCode.INTERNAL_ERROR: (500, "服务器内部错误"),
}


class APIError(Exception):
    """业务异常。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        meta = ERROR_META.get(code, (400, "请求处理失败"))
        self.code = code
        self.message = message or meta[1]
        self.status_code = status_code or meta[0]
        self.detail = detail
        super().__init__(f"{self.code}: {self.message}")


def not_found(message: str = "资源不存在") -> APIError:
    return APIError(ErrorCode.NOT_FOUND, message)
