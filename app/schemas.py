"""请求体校验模型（Pydantic v2）。

服务端校验是唯一权威：前端校验只用于提升体验，不能替代此处。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models import Difficulty, QuestionType


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=4, max_length=64)
    displayName: str | None = Field(default=None, max_length=40)

    @field_validator("username")
    @classmethod
    def _username_charset(cls, value: str) -> str:
        value = value.strip()
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用户名只能包含字母、数字、下划线和短横线")
        return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=64)


class CreateSessionRequest(BaseModel):
    questionCount: int = Field(default=10)
    categoryId: str | None = None
    difficulty: str | None = None

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, value: str | None) -> str | None:
        if value in (None, "", "ALL"):
            return None
        if value not in Difficulty.ALL:
            raise ValueError("难度只能是 EASY/MEDIUM/HARD")
        return value


class SaveAnswerRequest(BaseModel):
    """保存答案。

    这里是**形状校验**（非空、不超长）；「这道题该填什么才算合法」
    属于题型相关的规则，交给 ``quiz_service.save_answer`` —— 它才拿得到
    题目的 qtype。单选/判断题仍是 A–D 键位，填空题是 1–60 字的文本。
    """

    answer: str = Field(min_length=1, max_length=60)

    @field_validator("answer")
    @classmethod
    def _answer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("答案不能为空")
        return value


class QuestionUpsertRequest(BaseModel):
    content: str = Field(min_length=4, max_length=500)
    qtype: str = Field(default="SINGLE")
    options: dict[str, str] = Field(default_factory=dict)
    correctAnswer: str = Field(min_length=1, max_length=120)
    #: 仅填空题使用：命中任意一个即算答对
    keywords: list[str] = Field(default_factory=list)
    explanation: str = Field(default="", max_length=1000)
    difficulty: str = Field(default=Difficulty.MEDIUM)
    categoryId: str
    sourceTitle: str | None = Field(default=None, max_length=120)
    sourceSection: str | None = Field(default=None, max_length=120)
    isActive: bool = True

    @field_validator("qtype")
    @classmethod
    def _qtype(cls, value: str) -> str:
        value = (value or "SINGLE").strip().upper()
        if value not in QuestionType.ALL:
            raise ValueError("题型只能是 SINGLE/JUDGE/BLANK")
        return value

    @field_validator("options")
    @classmethod
    def _options(cls, value: dict[str, str] | None) -> dict[str, str]:
        return {str(k).upper(): str(v) for k, v in (value or {}).items()}

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        items = []
        for item in value:
            # 允许前端直接用顿号分隔的一整串
            items.extend(part.strip() for part in str(item).split("、"))
        return [item for item in items if item]

    @field_validator("correctAnswer")
    @classmethod
    def _correct(cls, value: str) -> str:
        return value.strip()

    @field_validator("difficulty")
    @classmethod
    def _difficulty(cls, value: str) -> str:
        if value not in Difficulty.ALL:
            raise ValueError("难度只能是 EASY/MEDIUM/HARD")
        return value


class ActiveToggleRequest(BaseModel):
    isActive: bool


class KnowledgeIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=20)
    course: str | None = Field(default=None, max_length=80)
    fileName: str | None = Field(default=None, max_length=160)


class RagExplainRequest(BaseModel):
    sessionId: str
    sessionQuestionId: str


class RecomputeRequest(BaseModel):
    userId: str | None = None
    all: bool = False


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump()
