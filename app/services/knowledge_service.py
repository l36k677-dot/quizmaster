"""知识库：把校史语料与题库整理成一座可浏览的知识展厅。

内容分两类来源：

1. **静态校史知识**（时间轴 / 人物档案 / 文化标识 / 易混淆辨析 / 主题导览）
   来自 ``data/seed/knowledge.json``。它是课程讲义的结构化整理，
   与题库标准答案同源，逐条标注了出处，便于溯源。

2. **与题库联动的部分**（题目记忆卡、分类分布）
   由数据库实时派生。这样题库改题后，知识库自动跟随，不需要维护两份数据。

页面走服务端渲染直出，JS 失效时全部内容仍然可读；前端的翻卡、筛选只是增强。
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import SEED_DIR
from app.models import (
    QUESTION_TYPE_LABELS,
    Category,
    Difficulty,
    Question,
    QuestionType,
)
from app.services.scoring import DIFFICULTY_LABELS

KNOWLEDGE_PATH = SEED_DIR / "knowledge.json"

# 文件级缓存：按 mtime 失效，改完 JSON 无需重启服务
_cache: dict = {"mtime": None, "data": None}

_EMPTY: dict = {
    "meta": {},
    "timeline": [],
    "people": [],
    "emblems": [],
    "contrasts": [],
    "themes": [],
}


def load_knowledge() -> dict:
    """读取并缓存 knowledge.json；文件缺失或损坏时返回空结构，不让页面崩。"""
    try:
        mtime = KNOWLEDGE_PATH.stat().st_mtime
    except OSError:
        return dict(_EMPTY)

    if _cache["data"] is None or _cache["mtime"] != mtime:
        try:
            raw = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(_EMPTY)
        merged = dict(_EMPTY)
        merged.update({k: v for k, v in raw.items() if k in _EMPTY})
        _cache["data"] = merged
        _cache["mtime"] = mtime
    return _cache["data"]


def _option_text(question: Question, key: str) -> str:
    return {
        "A": question.option_a,
        "B": question.option_b,
        "C": question.option_c,
        "D": question.option_d,
    }.get(key, "")


def to_card(question: Question) -> dict:
    """把一道题变成一张「记忆卡」：正面只给题干线索，背面给答案、解析与出处。"""
    qtype = question.qtype or QuestionType.SINGLE
    key = question.correct_answer
    if qtype == QuestionType.BLANK:
        # 填空题没有选项，卡片背面给出参考答案；关键字作为「判分要点」另列，
        # 方便一眼看出「答到什么程度算对」。
        words = question.keywords()
        answer_text = "、".join(words) or key
    else:
        answer_text = _option_text(question, key)
    return {
        "id": question.id,
        "prompt": question.content,
        "qtype": qtype,
        "qtypeLabel": QUESTION_TYPE_LABELS.get(qtype, "单选题"),
        "answerKey": key,
        "answerText": answer_text,
        "keywords": question.keywords() if qtype == QuestionType.BLANK else [],
        "explanation": question.explanation,
        "difficulty": question.difficulty,
        "difficultyLabel": DIFFICULTY_LABELS.get(question.difficulty, question.difficulty),
        "source": (
            f"{question.source_title} · {question.source_section}"
            if question.source_title and question.source_section
            else (question.source_title or "题库自带解析")
        ),
        "sourceTitle": question.source_title or "",
        "sourceSection": question.source_section or "",
    }


def _load_questions(db: Session) -> list[Question]:
    return list(
        db.scalars(
            select(Question)
            .where(Question.is_active.is_(True))
            .order_by(Question.created_at, Question.id)
        ).all()
    )


def overview(db: Session) -> dict:
    """组装知识库页面所需的全部数据。"""
    kb = load_knowledge()
    questions = _load_questions(db)

    categories = list(
        db.scalars(
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order, Category.name)
        ).all()
    )

    # knowledge.json 的 themes 按「分类名」组织，而 questions 存的是 category_id，
    # 这里先建映射再分组，避免两边对不上导致卡片为空。
    name_by_id = {category.id: category.name for category in categories}

    by_category: dict[str, list[dict]] = {}
    for question in questions:
        key = name_by_id.get(question.category_id, question.category_id)
        by_category.setdefault(key, []).append(to_card(question))

    themes: list[dict] = []
    for theme in kb.get("themes", []):
        cards = by_category.get(theme.get("key"), [])
        themes.append(
            {
                **theme,
                "count": len(cards),
                "cards": cards,
                "easyCount": sum(1 for c in cards if c["difficulty"] == Difficulty.EASY),
                "mediumCount": sum(1 for c in cards if c["difficulty"] == Difficulty.MEDIUM),
                "hardCount": sum(1 for c in cards if c["difficulty"] == Difficulty.HARD),
            }
        )

    # knowledge.json 里没登记的分类（例如管理员后加的）也要能出现在页面上
    known_keys = {t.get("key") for t in themes}
    for category in categories:
        if category.name in known_keys:
            continue
        cards = by_category.get(category.name, [])
        themes.append(
            {
                "key": category.name,
                "label": category.name,
                "accent": "blue",
                "lead": category.description or "该主题的知识卡片。",
                "keywords": [],
                "source": "题库自动归集",
                "count": len(cards),
                "cards": cards,
                "easyCount": sum(1 for c in cards if c["difficulty"] == Difficulty.EASY),
                "mediumCount": sum(1 for c in cards if c["difficulty"] == Difficulty.MEDIUM),
                "hardCount": sum(1 for c in cards if c["difficulty"] == Difficulty.HARD),
            }
        )

    timeline = kb.get("timeline", [])
    people = kb.get("people", [])

    return {
        "meta": kb.get("meta", {}),
        "timeline": timeline,
        "people": people,
        "emblems": kb.get("emblems", []),
        "contrasts": kb.get("contrasts", []),
        "themes": themes,
        "cardTotal": len(questions),
    }
