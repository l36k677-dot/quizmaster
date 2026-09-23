"""pytest 夹具：使用独立的临时 SQLite，避免污染开发数据库。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DB_PATH = Path(tempfile.gettempdir()) / "quizmaster_pytest.db"

# 必须在导入 app 之前设置，配置模块在导入时读取环境变量
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "pytest-secret-key"
os.environ["REWARD_ENABLED"] = "true"
os.environ["RAG_ENABLED"] = "false"      # 测试不调用外部模型
os.environ["TEST_TIME_LIMIT_SECONDS"] = "5"
os.environ["AI_API_KEY"] = ""

import pytest  # noqa: E402

ADMIN = {"username": "admin", "password": "QuizAdmin@2026"}
STUDENT = {"username": "test", "password": "test36"}


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    """建库并写入种子数据。"""
    if DB_PATH.exists():
        DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        extra = Path(str(DB_PATH) + suffix)
        if extra.exists():
            extra.unlink()

    from app.core.db import SessionLocal, engine, init_db
    from app.models import Category, Question, ScoreRule, User
    from app.core.security import hash_password, new_id
    from app.services import rewards as reward_service

    init_db()
    with SessionLocal() as db:
        admin = User(
            id=new_id("u_"), username="admin", password_hash=hash_password("QuizAdmin@2026"),
            role="ADMIN", display_name="系统管理员",
        )
        student = User(
            id=new_id("u_"), username="test", password_hash=hash_password("test36"),
            role="STUDENT", display_name="刘珂",
        )
        db.add_all([admin, student])

        categories = [
            Category(id="cat_a", name="创校与发展", sort_order=10),
            Category(id="cat_b", name="人物与校友", sort_order=20),
        ]
        db.add_all(categories)
        db.flush()

        # 每个分类 8 道题，保证 5 题可抽、10 题不足时能触发 QUESTION_NOT_ENOUGH
        for index in range(8):
            db.add(Question(
                id=f"q_a_{index}", content=f"创校类题目 {index}",
                option_a="A 选项", option_b="B 选项", option_c="C 选项", option_d="D 选项",
                correct_answer="A", explanation="解析", difficulty="EASY",
                category_id="cat_a", created_by=admin.id,
            ))
            db.add(Question(
                id=f"q_b_{index}", content=f"人物类题目 {index}",
                option_a="A 选项", option_b="B 选项", option_c="C 选项", option_d="D 选项",
                correct_answer="B", explanation="解析", difficulty="MEDIUM",
                category_id="cat_b", created_by=admin.id,
            ))
        for item in reward_service.ACHIEVEMENT_SEED:
            from app.models import Achievement
            db.add(Achievement(**item, is_active=True))
        db.add(ScoreRule(version="A-1", scoring_mode="ACCURACY", payload_json="{}"))
        db.commit()

        # 题型专用分类：10 道填空 + 10 道判断，正好 20 道。
        # 抽满 20 题就能确定性地拿到全部两种题型 —— 抽题接口没有题型筛选，
        # 但测试需要「一定是填空题」这种确定性，这是最省的实现方式。
        db.add(Category(id="cat_types", name="题型测试", sort_order=30))
        db.flush()
        for index in range(10):
            db.add(Question(
                id=f"q_blank_{index}", qtype="BLANK",
                content=f"填空题 {index}：请答出关键词 kw{index}",
                option_a="", option_b="", option_c="", option_d="",
                correct_answer=f"参考答案 {index}",
                keywords_json=json.dumps([f"kw{index}", f"关键词{index}"], ensure_ascii=False),
                explanation="解析", difficulty="MEDIUM",
                category_id="cat_types", created_by=admin.id,
            ))
            db.add(Question(
                id=f"q_judge_{index}", qtype="JUDGE",
                content=f"判断题 {index}：这句话是正确的。",
                option_a="正确", option_b="错误", option_c="", option_d="",
                correct_answer="A", keywords_json="[]",
                explanation="解析", difficulty="EASY",
                category_id="cat_types", created_by=admin.id,
            ))
        db.commit()

    yield

    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(DB_PATH) + suffix)
        if path.exists():
            path.unlink()


def login(client, credentials) -> None:
    """登录并断言成功，供各 fixture 复用。"""
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200, response.text


@pytest.fixture()
def make_client():
    """创建独立登录态的 TestClient，用于越权等需要多用户的场景。"""
    from fastapi.testclient import TestClient
    from app.main import app

    opened = []

    def _make(credentials=None):
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        if credentials:
            login(client, credentials)
        return client

    yield _make

    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture()
def client(make_client):
    return make_client()


@pytest.fixture()
def student_client(make_client):
    return make_client(STUDENT)


@pytest.fixture()
def admin_client(make_client):
    return make_client(ADMIN)


@pytest.fixture()
def other_client(make_client):
    """第三个用户，用于越权测试。"""
    client = make_client()
    response = client.post(
        "/api/auth/register",
        json={"username": "intruder", "password": "intruder-pass", "displayName": "越权测试"},
    )
    assert response.status_code == 200, response.text
    return client
