"""ORM 数据模型。

与设计文档第 13 章一一对应：users / categories / questions /
quiz_sessions / session_questions / quiz_answers / RAG 三表 /
achievements / user_achievements / score_rules。

设计要点：
1. 会话创建时把题目与标准答案写入 session_questions 快照，
   之后管理员编辑题库不会改变历史成绩。
2. session_question_id 在 quiz_answers 上唯一，保证一题只有一个最终答案。
3. (user_id, achievement_code) 唯一，保证成就不会重复解锁。
"""

from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.core.db import Base


# ------------------------------------------------------------------ 枚举值
class Role:
    STUDENT = "STUDENT"
    ADMIN = "ADMIN"
    ALL = (STUDENT, ADMIN)


class Difficulty:
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"
    ALL = (EASY, MEDIUM, HARD)


class QuestionType:
    """题型。

    * ``SINGLE`` 单选：四个选项 A–D。
    * ``JUDGE``  判断：只有「正确 / 错误」两个选项，复用 A/B 两个键位。
    * ``BLANK``  填空：没有选项，判分按关键字命中（见 ``scoring.judge_blank``）。

    判断题刻意复用选项机制（A=正确、B=错误），这样抽题、快照、结算、
    结果页的既有链路一行都不用改；只有填空需要一条独立的输入与判分分支。
    """

    SINGLE = "SINGLE"
    JUDGE = "JUDGE"
    BLANK = "BLANK"
    ALL = (SINGLE, JUDGE, BLANK)


QUESTION_TYPE_LABELS: dict[str, str] = {
    QuestionType.SINGLE: "单选题",
    QuestionType.JUDGE: "判断题",
    QuestionType.BLANK: "填空题",
}

#: 判断题固定选项：A=正确、B=错误。判分与单选完全同构。
JUDGE_OPTIONS: dict[str, str] = {"A": "正确", "B": "错误"}


# ------------------------------------------------------------------ 关键字
def load_keywords(raw: str | None) -> list[str]:
    """把 keywords_json 还原成字符串列表；坏数据一律降级为空列表。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        # 兼容历史/手工写入的「顿号分隔」写法，读得回来就不会判不动分
        return [part.strip() for part in str(raw).split("、") if part.strip()]
    if isinstance(data, str):
        return [part.strip() for part in data.split("、") if part.strip()]
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    return []


def dump_keywords(words: object) -> str:
    """把关键字列表规范成 JSON 字符串落库。"""
    if isinstance(words, str):
        items = [part.strip() for part in words.split("、")]
    elif isinstance(words, (list, tuple, set)):
        items = [str(item).strip() for item in words]
    else:
        items = []
    return json.dumps([item for item in items if item], ensure_ascii=False)


#: 模块内别名，供 ORM 方法调用
_load_keywords = load_keywords


class SessionStatus:
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
    #: 终态集合：只有终态会话参与成绩、排行与奖励统计
    FINAL = (COMPLETED, TIMEOUT)


class Grade:
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    PASS = "PASS"
    NEEDS_WORK = "NEEDS_WORK"


class RagStatus:
    SUCCESS = "SUCCESS"
    NO_CONTEXT = "NO_CONTEXT"
    FAILED = "FAILED"


# ------------------------------------------------------------------ 用户
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=Role.STUDENT)
    display_name: Mapped[str | None] = mapped_column(String(50))
    study_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_quiz_date: Mapped[date | None] = mapped_column(Date)
    #: 是否看过新手指引（荷宝导览）。跟着账号走而不是只写浏览器本地：
    #: 换设备后不该被同一份指引再拦一次。见 app/services/tour.py。
    tour_seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    sessions: Mapped[list["QuizSession"]] = relationship(back_populates="user")
    achievements: Mapped[list["UserAchievement"]] = relationship(back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


# ------------------------------------------------------------------ 题库
class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    questions: Mapped[list["Question"]] = relationship(back_populates="category")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: SINGLE / JUDGE / BLANK，见 QuestionType
    qtype: Mapped[str] = mapped_column(
        String(10), nullable=False, default=QuestionType.SINGLE, index=True
    )
    #: 单选与判断题：A–D（判断题只有 A/B）；填空题留空，
    #: 正确答案以参考答案 + 关键字承载
    option_a: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_b: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_c: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_d: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 单选/判断题是键位（"A"）；填空题是参考答案原文（用于展示与完全匹配兜底）
    correct_answer: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: 填空题的关键字列表（JSON 数组）。命中任意一个即算答出要点。
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, default=Difficulty.MEDIUM)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(120))
    source_section: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    category: Mapped[Category] = relationship(back_populates="questions")

    __table_args__ = (
        Index("ix_questions_filter", "is_active", "category_id", "difficulty"),
        Index("ix_questions_updated", "updated_at"),
    )

    def option_map(self) -> dict[str, str]:
        """只返回真正存在的选项。

        单选返回 A–D 四项；判断题返回 A/B 两项；填空题返回空字典。
        用「非空」而不是「按题型分支」来判定，是为了让历史数据里
        残留的空选项也不会渲染出一个空按钮。
        """
        pairs = (
            ("A", self.option_a),
            ("B", self.option_b),
            ("C", self.option_c),
            ("D", self.option_d),
        )
        return {key: value for key, value in pairs if (value or "").strip()}

    @property
    def is_blank(self) -> bool:
        return self.qtype == QuestionType.BLANK

    def keywords(self) -> list[str]:
        return _load_keywords(self.keywords_json)


# ------------------------------------------------------------------ 答题会话
class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    category_filter: Mapped[str | None] = mapped_column(String(32))
    difficulty_filter: Mapped[str | None] = mapped_column(String(10))
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SessionStatus.IN_PROGRESS
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int | None] = mapped_column(Integer, index=True)
    correct_count: Mapped[int | None] = mapped_column(Integer)
    wrong_count: Mapped[int | None] = mapped_column(Integer)
    unanswered_count: Mapped[int | None] = mapped_column(Integer, default=0)
    rule_version: Mapped[str] = mapped_column(String(16), nullable=False, default="A-1")
    grade: Mapped[str | None] = mapped_column(String(16))
    score_breakdown_json: Mapped[str | None] = mapped_column(Text)
    sp_earned: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")
    session_questions: Mapped[list["SessionQuestion"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionQuestion.order_no",
    )

    __table_args__ = (
        Index("ix_sessions_user_created", "user_id", "created_at"),
        Index("ix_sessions_status_score", "status", "score"),
    )

    @property
    def is_final(self) -> bool:
        return self.status in SessionStatus.FINAL


class SessionQuestion(Base):
    """一局的题目与标准答案快照。"""

    __tablename__ = "session_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("quiz_sessions.id"), nullable=False)
    original_question_id: Mapped[str | None] = mapped_column(String(32), index=True)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    qtype_snapshot: Mapped[str] = mapped_column(
        String(10), nullable=False, default=QuestionType.SINGLE
    )
    option_a_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_b_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_c_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    option_d_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correct_answer_snapshot: Mapped[str] = mapped_column(
        String(120), nullable=False, default=""
    )
    keywords_json_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    explanation_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category_snapshot: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    difficulty_snapshot: Mapped[str] = mapped_column(String(10), nullable=False)
    source_title_snapshot: Mapped[str | None] = mapped_column(String(120))
    source_section_snapshot: Mapped[str | None] = mapped_column(String(120))

    session: Mapped[QuizSession] = relationship(back_populates="session_questions")
    answer: Mapped["QuizAnswer | None"] = relationship(
        back_populates="session_question", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "order_no", name="uq_session_order"),
        UniqueConstraint("session_id", "original_question_id", name="uq_session_question"),
    )

    def option_map(self) -> dict[str, str]:
        pairs = (
            ("A", self.option_a_snapshot),
            ("B", self.option_b_snapshot),
            ("C", self.option_c_snapshot),
            ("D", self.option_d_snapshot),
        )
        return {key: value for key, value in pairs if (value or "").strip()}

    def keywords(self) -> list[str]:
        return load_keywords(self.keywords_json_snapshot)

    def to_public_dict(self, order_no: int | None = None) -> dict:
        """给答题页用的安全结构：绝不包含标准答案，也绝不包含判分关键字。"""
        return {
            "sessionQuestionId": self.id,
            "orderNo": order_no if order_no is not None else self.order_no,
            "content": self.content_snapshot,
            "qtype": self.qtype_snapshot or QuestionType.SINGLE,
            "options": self.option_map(),
            "difficulty": self.difficulty_snapshot,
            "category": self.category_snapshot,
        }


class QuizAnswer(Base):
    """用户对某题的最终选择，一题只有一条。"""

    __tablename__ = "quiz_answers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_question_id: Mapped[str] = mapped_column(
        ForeignKey("session_questions.id"), nullable=False, unique=True
    )
    #: 单选/判断题存键位（"A"）；填空题存用户输入的原文
    user_answer: Mapped[str] = mapped_column(String(120), nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    session_question: Mapped[SessionQuestion] = relationship(back_populates="answer")


# ------------------------------------------------------------------ RAG
class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    course: Mapped[str | None] = mapped_column(String(80))
    file_name: Mapped[str | None] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="READY")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False)
    section_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class RagExplanation(Base):
    __tablename__ = "rag_explanations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_question_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_answer: Mapped[str | None] = mapped_column(String(120))
    model_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RagStatus.SUCCESS)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_question_id", "user_answer", name="uq_rag_cache"),
    )


# ------------------------------------------------------------------ 奖励
class Achievement(Base):
    __tablename__ = "achievements"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    achievement_code: Mapped[str] = mapped_column(
        ForeignKey("achievements.code"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(String(32))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="achievements")
    achievement: Mapped[Achievement] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "achievement_code", name="uq_user_achievement"),
    )


class ScoreRule(Base):
    """判分口径版本表（默认只有一条 A-1）。"""

    __tablename__ = "score_rules"

    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    scoring_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="ACCURACY")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
