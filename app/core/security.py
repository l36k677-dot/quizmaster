"""安全工具：密码哈希与签名会话 Cookie。

密码使用标准库 PBKDF2-HMAC-SHA256（60 万次迭代），不引入额外依赖；
会话使用 itsdangerous 对 Cookie 做签名，防止客户端伪造 user_id 与 role。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import settings

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
SESSION_MAX_AGE = 60 * 60 * 12  # 12 小时
SESSION_COOKIE_NAME = "qm_session"


# ------------------------------------------------------------------ 密码
def hash_password(password: str) -> str:
    """返回 pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>。"""
    if not password or len(password) < 4:
        raise ValueError("密码长度至少 4 位")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比对，避免时序侧信道。"""
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ 会话
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="quizmaster-session")


def issue_session_token(user_id: str, role: str) -> str:
    return _serializer.dumps({"uid": user_id, "role": role})


def read_session_token(token: str) -> dict[str, Any] | None:
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    except Exception:
        return None


# ------------------------------------------------------------------ 其他
def new_id(prefix: str = "") -> str:
    """生成 URL 安全的短 ID。"""
    value = secrets.token_hex(12)
    return f"{prefix}{value}" if prefix else value


def mask_secret(value: str, keep: int = 4) -> str:
    """日志与页面上展示密钥时脱敏。"""
    if not value:
        return "(未配置)"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}"
