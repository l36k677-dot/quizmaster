"""时间工具：全局统一使用 UTC，展示层再转东八区。

数据库统一存 naive UTC，避免 SQLite 丢失时区信息后产生歧义。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

#: 东八区（展示用）
CST = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    """当前 UTC 时间（naive）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc(value: datetime | None) -> datetime | None:
    """把带时区的时间转成 naive UTC。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def today_cst() -> date:
    """当前东八区自然日，用于连续研习天数判定。"""
    return datetime.now(CST).date()


def days_between(earlier: date, later: date) -> int:
    return (later - earlier).days


def format_duration(seconds: int | None) -> str:
    """把秒数格式化为 mm:ss。"""
    if seconds is None:
        return "--:--"
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def format_datetime_cst(value) -> str:
    """把 UTC 时间（datetime 或 ISO 字符串）格式化为东八区可读文本。

    服务层序列化后传给模板的往往是 ISO 字符串，这里统一兼容。
    """
    if value is None or value == "":
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CST).strftime("%Y-%m-%d %H:%M")
