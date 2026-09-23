"""数据库引擎与会话管理。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _create_engine():
    url = settings.sqlalchemy_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            # 外键约束与 WAL 模式：保证引用完整性和并发读写稳定性
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return eng


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本使用的上下文管理器，异常自动回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """建表 + 补齐新增列（等价于轻量迁移）。"""
    from app import models  # noqa: F401  确保模型被注册

    Base.metadata.create_all(bind=engine)
    added = ensure_columns()
    for table, columns in added.items():
        print(f"[migrate] {table} 补齐列：" + "、".join(columns))


#: ADDL 语句里给不同类型列的兜底默认值
_FALLBACK_DEFAULT = {
    "INTEGER": "0",
    "FLOAT": "0",
    "NUMERIC": "0",
    "BOOLEAN": "0",
    "DATETIME": None,
    "DATE": None,
}


def _default_literal(column) -> str | None:
    """为一个新增列推导 DEFAULT 子句的字面量；推导不出就返回 None。"""
    # 1) 模型里的 Python 端默认值（本项目新增列都走这条）
    arg = getattr(getattr(column, "default", None), "arg", None)
    if isinstance(arg, bool):
        return "1" if arg else "0"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"

    # 2) 服务端默认值
    server = getattr(column, "server_default", None)
    if server is not None and getattr(server, "arg", None) is not None:
        arg = server.arg
        return arg.text if hasattr(arg, "text") else str(arg)

    # 3) 可空列不需要默认值
    if column.nullable:
        return None

    # 4) 兜底：按类型给一个中性值。NOT NULL 列没有默认值时，SQLite 的
    #    ALTER TABLE ADD COLUMN 会直接报错，所以这里必须给出一个值。
    type_name = column.type.__class__.__name__.upper()
    for key, value in _FALLBACK_DEFAULT.items():
        if key in type_name:
            return value
    return "''"


def ensure_columns() -> dict[str, list[str]]:
    """为已存在的表补齐模型中新增的列（只增不改不删）。

    为什么需要它：``create_all`` 只建**缺失的表**，不会给已存在的表加列。
    于是「给 questions 加一个 qtype」在旧库上会直接报 no such column。
    课程项目用的是 SQLite，没有 Alembic；相比「删库重建」（会丢掉全部
    答题历史与排行榜），就地补列是更安全的选择。

    返回 ``{表名: [新增列名, ...]}``，便于调用方打印迁移记录。
    """
    from sqlalchemy import inspect, text

    from app import models  # noqa: F401

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: dict[str, list[str]] = {}

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if column.primary_key:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                clause = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
                if not column.nullable:
                    clause += " NOT NULL"
                default = _default_literal(column)
                if default is not None:
                    clause += f" DEFAULT {default}"
                conn.execute(text(clause))
                added.setdefault(table.name, []).append(column.name)
                # 同一张表可能有多个新列，需要刷新已存在列的快照
                present.add(column.name)

    return added
