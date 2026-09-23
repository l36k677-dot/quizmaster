"""题库服务：分类、可用题量、抽题与 CRUD。"""

from __future__ import annotations

import random
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import APIError, ErrorCode
from app.core.security import new_id
from app.models import (
    JUDGE_OPTIONS,
    QUESTION_TYPE_LABELS,
    Category,
    Difficulty,
    Question,
    QuestionType,
    User,
    dump_keywords,
)

OPTION_KEYS = ("A", "B", "C", "D")


def normalize_qtype(value: str | None) -> str:
    """题型归一化；空值按单选处理，非法值直接拒绝。"""
    if value in (None, ""):
        return QuestionType.SINGLE
    qtype = str(value).strip().upper()
    if qtype not in QuestionType.ALL:
        raise APIError(ErrorCode.VALIDATION_ERROR, "题型只能是 SINGLE/JUDGE/BLANK")
    return qtype


def list_categories(db: Session, *, only_active: bool = True) -> list[Category]:
    stmt = select(Category).order_by(Category.sort_order, Category.name)
    if only_active:
        stmt = stmt.where(Category.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def category_map(db: Session) -> dict[str, str]:
    return {c.id: c.name for c in db.execute(select(Category)).scalars()}


def validate_filters(db: Session, category_id: str | None, difficulty: str | None) -> None:
    if category_id and db.get(Category, category_id) is None:
        raise APIError(ErrorCode.VALIDATION_ERROR, "分类不存在")
    if difficulty and difficulty not in Difficulty.ALL:
        raise APIError(ErrorCode.VALIDATION_ERROR, "难度只能是 EASY/MEDIUM/HARD")


def available_count(db: Session, category_id: str | None, difficulty: str | None) -> int:
    stmt = select(func.count(Question.id)).where(Question.is_active.is_(True))
    if category_id:
        stmt = stmt.where(Question.category_id == category_id)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    return int(db.execute(stmt).scalar_one())


def draw_questions(
    db: Session, *, count: int, category_id: str | None, difficulty: str | None
) -> list[Question]:
    """随机抽取不重复题目；不足时抛 QUESTION_NOT_ENOUGH，绝不缩水成局。"""
    stmt = select(Question).where(Question.is_active.is_(True))
    if category_id:
        stmt = stmt.where(Question.category_id == category_id)
    if difficulty:
        stmt = stmt.where(Question.difficulty == difficulty)
    pool = list(db.execute(stmt).scalars())
    if len(pool) < count:
        raise APIError(
            ErrorCode.QUESTION_NOT_ENOUGH,
            f"当前条件下仅有 {len(pool)} 道可用题目，请减少题量或放宽筛选",
        )
    return random.sample(pool, count)


# ------------------------------------------------------------------ 管理端
def admin_list_questions(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    category_id: str | None = None,
    difficulty: str | None = None,
    qtype: str | None = None,
    active_only: bool = False,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(50, int(page_size)))
    conditions = []
    if keyword:
        conditions.append(Question.content.like(f"%{keyword}%"))
    if category_id:
        conditions.append(Question.category_id == category_id)
    if difficulty:
        conditions.append(Question.difficulty == difficulty)
    if qtype:
        conditions.append(Question.qtype == normalize_qtype(qtype))
    if active_only:
        conditions.append(Question.is_active.is_(True))

    count_stmt = select(func.count(Question.id))
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
    total = int(db.execute(count_stmt).scalar_one())

    stmt = select(Question)
    for cond in conditions:
        stmt = stmt.where(cond)
    stmt = stmt.order_by(Question.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list(db.execute(stmt).scalars())
    names = category_map(db)

    return {
        "items": [serialize_question(q, names.get(q.category_id, "-")) for q in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size),
    }


def serialize_question(question: Question, category_name: str) -> dict[str, Any]:
    qtype = question.qtype or QuestionType.SINGLE
    return {
        "id": question.id,
        "content": question.content,
        "qtype": qtype,
        "qtypeLabel": QUESTION_TYPE_LABELS.get(qtype, "单选题"),
        "options": question.option_map(),
        # 单选/判断题是键位；填空题是参考答案原文
        "correctAnswer": question.correct_answer,
        "keywords": question.keywords() if qtype == QuestionType.BLANK else [],
        "explanation": question.explanation,
        "difficulty": question.difficulty,
        "categoryId": question.category_id,
        "categoryName": category_name,
        "sourceTitle": question.source_title,
        "sourceSection": question.source_section,
        "isActive": question.is_active,
        "updatedAt": question.updated_at.isoformat() if question.updated_at else None,
    }


def _options_from_payload(payload: dict[str, Any], qtype: str) -> dict[str, str]:
    """按题型补齐选项，返回值**恒定包含 A–D 四个键**。

    判断题固定「A=正确 / B=错误」，C/D 留空；填空题四项全空。之所以不
    按题型返回不同的键集合，是因为调用方要写 option_a..option_d 四个列，
    少一个键就会 KeyError。空的选项由 ``option_map()`` 在序列化时过滤掉，
    不会渲染出空按钮。
    """
    blank = {key: "" for key in OPTION_KEYS}
    if qtype == QuestionType.JUDGE:
        blank.update(JUDGE_OPTIONS)
        return blank
    if qtype == QuestionType.BLANK:
        return blank
    raw = payload.get("options") or {}
    return {key: str(raw.get(key, "") or "").strip() for key in OPTION_KEYS}


def create_question(db: Session, admin: User, payload: dict[str, Any]) -> Question:
    _validate_question_payload(db, payload)
    qtype = normalize_qtype(payload.get("qtype"))
    options = _options_from_payload(payload, qtype)
    question = Question(
        id=new_id("q_"),
        content=payload["content"].strip(),
        qtype=qtype,
        option_a=options["A"],
        option_b=options["B"],
        option_c=options["C"],
        option_d=options["D"],
        correct_answer=payload["correctAnswer"].strip(),
        keywords_json=dump_keywords(payload.get("keywords") or []),
        explanation=(payload.get("explanation") or "").strip(),
        difficulty=payload.get("difficulty") or Difficulty.MEDIUM,
        category_id=payload["categoryId"],
        source_title=payload.get("sourceTitle"),
        source_section=payload.get("sourceSection"),
        is_active=bool(payload.get("isActive", True)),
        created_by=admin.id,
    )
    db.add(question)
    db.commit()
    return question


def update_question(db: Session, question_id: str, payload: dict[str, Any]) -> Question:
    question = db.get(Question, question_id)
    if question is None:
        raise APIError(ErrorCode.NOT_FOUND, "题目不存在")
    _validate_question_payload(db, payload)
    qtype = normalize_qtype(payload.get("qtype"))
    options = _options_from_payload(payload, qtype)
    question.content = payload["content"].strip()
    question.qtype = qtype
    question.option_a = options["A"]
    question.option_b = options["B"]
    question.option_c = options["C"]
    question.option_d = options["D"]
    question.correct_answer = payload["correctAnswer"].strip()
    question.keywords_json = dump_keywords(payload.get("keywords") or [])
    question.explanation = (payload.get("explanation") or "").strip()
    question.difficulty = payload.get("difficulty") or Difficulty.MEDIUM
    question.category_id = payload["categoryId"]
    question.source_title = payload.get("sourceTitle")
    question.source_section = payload.get("sourceSection")
    if "isActive" in payload:
        question.is_active = bool(payload["isActive"])
    db.commit()

    # 注意：历史会话使用快照，此处修改不会改变既有成绩（核心不可变规则 2）
    return question


def set_question_active(db: Session, question_id: str, is_active: bool) -> Question:
    question = db.get(Question, question_id)
    if question is None:
        raise APIError(ErrorCode.NOT_FOUND, "题目不存在")
    question.is_active = is_active
    db.commit()
    return question


def _validate_question_payload(db: Session, payload: dict[str, Any]) -> None:
    content = (payload.get("content") or "").strip()
    if len(content) < 4:
        raise APIError(ErrorCode.VALIDATION_ERROR, "题干至少 4 个字符")
    qtype = normalize_qtype(payload.get("qtype"))
    answer = (payload.get("correctAnswer") or "").strip()

    if qtype == QuestionType.SINGLE:
        options = payload.get("options") or {}
        for key in OPTION_KEYS:
            if not (options.get(key) or "").strip():
                raise APIError(ErrorCode.VALIDATION_ERROR, f"选项 {key} 不能为空")
        if answer.upper() not in OPTION_KEYS:
            raise APIError(ErrorCode.VALIDATION_ERROR, "单选题的标准答案只能是 A/B/C/D")

    elif qtype == QuestionType.JUDGE:
        # 判断题只有「正确 / 错误」两个选项，答案固定为 A 或 B
        if answer.upper() not in ("A", "B"):
            raise APIError(ErrorCode.VALIDATION_ERROR, "判断题的标准答案只能是 A（正确）或 B（错误）")

    else:  # BLANK
        if not answer:
            raise APIError(ErrorCode.VALIDATION_ERROR, "填空题必须填写参考答案")
        if len(answer) > 120:
            raise APIError(ErrorCode.VALIDATION_ERROR, "填空题参考答案过长（上限 120 字）")
        words = payload.get("keywords") or []
        if isinstance(words, str):
            words = [part.strip() for part in words.split("、")]
        words = [str(w).strip() for w in words if str(w).strip()]
        if not words:
            raise APIError(
                ErrorCode.VALIDATION_ERROR,
                "填空题至少需要一个关键字，否则无法判分（参考答案仅作兜底）",
            )
        if len(words) > 8:
            raise APIError(ErrorCode.VALIDATION_ERROR, "填空题关键字最多 8 个")
        for word in words:
            if len(word) > 30:
                raise APIError(ErrorCode.VALIDATION_ERROR, f"关键字「{word}」过长（上限 30 字）")

    difficulty = payload.get("difficulty") or Difficulty.MEDIUM
    if difficulty not in Difficulty.ALL:
        raise APIError(ErrorCode.VALIDATION_ERROR, "难度只能是 EASY/MEDIUM/HARD")
    if db.get(Category, payload.get("categoryId") or "") is None:
        raise APIError(ErrorCode.VALIDATION_ERROR, "分类不存在")


def question_stats(db: Session) -> dict[str, Any]:
    """题库统计：总数、启用数、分类分布、难度分布。"""
    names = category_map(db)
    total = int(db.execute(select(func.count(Question.id))).scalar_one())
    active = int(
        db.execute(select(func.count(Question.id)).where(Question.is_active.is_(True))).scalar_one()
    )

    by_category = []
    for category in list_categories(db, only_active=False):
        cnt = int(
            db.execute(
                select(func.count(Question.id)).where(Question.category_id == category.id)
            ).scalar_one()
        )
        by_category.append({"categoryId": category.id, "name": category.name, "count": cnt})

    by_difficulty = []
    for difficulty in Difficulty.ALL:
        cnt = int(
            db.execute(
                select(func.count(Question.id)).where(Question.difficulty == difficulty)
            ).scalar_one()
        )
        by_difficulty.append({"difficulty": difficulty, "count": cnt})

    by_type = []
    for qtype in QuestionType.ALL:
        cnt = int(
            db.execute(
                select(func.count(Question.id)).where(Question.qtype == qtype)
            ).scalar_one()
        )
        by_type.append({
            "qtype": qtype,
            "label": QUESTION_TYPE_LABELS.get(qtype, qtype),
            "count": cnt,
        })

    return {
        "total": total,
        "active": active,
        "inactive": total - active,
        "byCategory": by_category,
        "byDifficulty": by_difficulty,
        "byType": by_type,
        "categoryMap": names,
    }
