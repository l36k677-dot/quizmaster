"""竞答会话服务：状态机、抽题快照、保存答案、提交判分。

状态机（设计文档 9.1 / 9.2）：

    IN_PROGRESS --主动提交--> COMPLETED
    IN_PROGRESS --到期------> TIMEOUT
    终态 --再次提交--> 原结果（幂等，不重复计分）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import settings
from app.core.errors import APIError, ErrorCode
from app.core.security import new_id
from app.models import (
    QuestionType,
    QuizAnswer,
    QuizSession,
    SessionQuestion,
    SessionStatus,
    User,
)
from app.services import question_service
from app.services.scoring import (
    BLANK_MAX_LEN,
    DIFFICULTY_LABELS,
    GRADE_LABELS,
    QUESTION_TYPE_LABELS,
    QuestionResult,
    answer_is_correct,
    evaluate,
    hit_keywords,
)

#: 允许的最短网络宽限：只用于请求恰好卡在截止瞬间到达的情况
GRACE_SECONDS = 3

#: 允许的题量
ALLOWED_COUNTS = (5, 10, 20)


def _now() -> datetime:
    """当前服务端时间（UTC naive）。单测可覆盖此函数。"""
    return utcnow()


def per_question_seconds() -> int:
    return settings.per_question_seconds


# ------------------------------------------------------------------ 创建
def create_session(
    db: Session,
    user: User,
    *,
    question_count: int,
    category_id: str | None = None,
    difficulty: str | None = None,
) -> QuizSession:
    if question_count not in ALLOWED_COUNTS:
        raise APIError(ErrorCode.VALIDATION_ERROR, "题量只能是 5、10 或 20")
    question_service.validate_filters(db, category_id, difficulty)

    picked = question_service.draw_questions(
        db, count=question_count, category_id=category_id, difficulty=difficulty
    )
    names = question_service.category_map(db)

    starts_at = _now()
    expires_at = starts_at + timedelta(seconds=per_question_seconds() * question_count)

    session = QuizSession(
        id=new_id("qs_"),
        user_id=user.id,
        category_filter=category_id,
        difficulty_filter=difficulty,
        question_count=question_count,
        status=SessionStatus.IN_PROGRESS,
        starts_at=starts_at,
        expires_at=expires_at,
        rule_version="A-1",
    )
    db.add(session)
    db.flush()

    # 冻结题目与标准答案快照：题库后续被编辑不影响本局
    for index, question in enumerate(picked, start=1):
        db.add(
            SessionQuestion(
                id=new_id("sq_"),
                session_id=session.id,
                original_question_id=question.id,
                order_no=index,
                content_snapshot=question.content,
                qtype_snapshot=question.qtype or QuestionType.SINGLE,
                option_a_snapshot=question.option_a,
                option_b_snapshot=question.option_b,
                option_c_snapshot=question.option_c,
                option_d_snapshot=question.option_d,
                correct_answer_snapshot=question.correct_answer,
                keywords_json_snapshot=question.keywords_json or "[]",
                explanation_snapshot=question.explanation,
                category_snapshot=names.get(question.category_id, ""),
                difficulty_snapshot=question.difficulty,
                source_title_snapshot=question.source_title,
                source_section_snapshot=question.source_section,
            )
        )
    db.commit()
    db.refresh(session)
    return session


# ------------------------------------------------------------------ 读取
def get_owned_session(db: Session, user: User, session_id: str) -> QuizSession:
    session = db.get(QuizSession, session_id)
    if session is None:
        raise APIError(ErrorCode.SESSION_NOT_FOUND)
    if session.user_id != user.id:
        # 不做存在性区分，避免越权探测
        raise APIError(ErrorCode.FORBIDDEN, "无权访问他人答题会话")
    return session


def is_timed_out(session: QuizSession, *, reference: datetime | None = None) -> bool:
    moment = reference or _now()
    return moment > session.expires_at


def is_timed_out_with_grace(session: QuizSession, *, reference: datetime | None = None) -> bool:
    moment = reference or _now()
    return moment > session.expires_at + timedelta(seconds=GRACE_SECONDS)


# ------------------------------------------------------------------ 保存答案
def save_answer(
    db: Session, user: User, session_id: str, session_question_id: str, answer: str
) -> dict[str, Any]:
    session = get_owned_session(db, user, session_id)

    if session.is_final:
        raise APIError(ErrorCode.SESSION_FINISHED, "本局已结束，不能继续作答")
    if is_timed_out(session):
        # 惰性结算：一旦越过截止时间，本局立即进入 TIMEOUT 终态
        finalize(db, session, timed_out=True)
        raise APIError(ErrorCode.SESSION_EXPIRED, "本局已超时，答案已按已保存内容结算")

    sq = db.get(SessionQuestion, session_question_id)
    if sq is None or sq.session_id != session.id:
        raise APIError(ErrorCode.NOT_IN_SESSION)

    # 按题型校验：单选/判断题是 A–D 键位，填空题是 1–60 字的文本。
    # 校验放在取到题目之后，因为「哪些答案合法」取决于这道题的题型。
    qtype = sq.qtype_snapshot or QuestionType.SINGLE
    raw = (answer or "").strip()
    if qtype == QuestionType.BLANK:
        if not raw:
            raise APIError(ErrorCode.INVALID_OPTION, "填空题答案不能为空")
        if len(raw) > BLANK_MAX_LEN:
            raise APIError(
                ErrorCode.INVALID_OPTION, f"填空题答案最多 {BLANK_MAX_LEN} 个字"
            )
        value = raw
    else:
        value = raw.upper()
        allowed = set(sq.option_map().keys()) or {"A", "B", "C", "D"}
        if value not in allowed:
            raise APIError(ErrorCode.INVALID_OPTION)

    record = db.execute(
        select(QuizAnswer).where(QuizAnswer.session_question_id == sq.id)
    ).scalar_one_or_none()
    if record is None:
        record = QuizAnswer(
            id=new_id("qa_"),
            session_question_id=sq.id,
            user_answer=value,
            answered_at=_now(),
        )
        db.add(record)
    else:
        # 同一题重复选择只更新最终答案，不新增记录
        record.user_answer = value
        record.answered_at = _now()
    db.commit()
    return {"sessionQuestionId": sq.id, "userAnswer": value, "answeredAt": record.answered_at.isoformat()}


# ------------------------------------------------------------------ 提交与判分
def finalize(db: Session, session: QuizSession, *, timed_out: bool) -> dict[str, Any] | None:
    """终态结算：判分 + 落库，必须在单个事务内完成。幂等。"""
    if session.is_final:
        return None

    questions = list(
        db.execute(
            select(SessionQuestion)
            .where(SessionQuestion.session_id == session.id)
            .order_by(SessionQuestion.order_no)
        ).scalars()
    )
    answers = {
        row.session_question_id: row
        for row in db.execute(
            select(QuizAnswer).where(
                QuizAnswer.session_question_id.in_([q.id for q in questions])
            )
        ).scalars()
    }

    results = [
        QuestionResult(
            order_no=sq.order_no,
            difficulty=sq.difficulty_snapshot,
            correct_answer=sq.correct_answer_snapshot,
            user_answer=(answers[sq.id].user_answer if sq.id in answers else None),
            category=sq.category_snapshot,
            qtype=sq.qtype_snapshot or QuestionType.SINGLE,
            keywords=tuple(sq.keywords()),
        )
        for sq in questions
    ]
    outcome = evaluate(results, mode="ACCURACY", rule_version=session.rule_version or "A-1")

    submitted_at = _now()
    # 超时场景下，用时按截止时间封顶，保证“用时”读数不超过本局时长上限
    effective_end = min(submitted_at, session.expires_at + timedelta(seconds=GRACE_SECONDS))
    duration = max(0, int((effective_end - session.starts_at).total_seconds()))
    duration = min(duration, session.question_count * per_question_seconds())

    session.status = SessionStatus.TIMEOUT if timed_out else SessionStatus.COMPLETED
    session.submitted_at = submitted_at
    session.duration_seconds = duration
    session.score = outcome.score
    session.correct_count = outcome.correct_count
    session.wrong_count = outcome.wrong_count
    session.unanswered_count = outcome.unanswered_count
    session.grade = outcome.grade
    session.score_breakdown_json = _dump_breakdown(outcome.breakdown)

    # 逐题回写 is_correct（成绩明细可查）。判分必须走同一个纯函数，
    # 否则「明细」与「总分」会出现两套口径。
    for sq in questions:
        record = answers.get(sq.id)
        if record is not None:
            record.is_correct = answer_is_correct(
                sq.qtype_snapshot or QuestionType.SINGLE,
                sq.correct_answer_snapshot,
                sq.keywords(),
                record.user_answer,
            )

    db.commit()
    return outcome.breakdown


def submit_session(db: Session, user: User, session_id: str) -> dict[str, Any]:
    """主动提交或触发超时结算。重复提交返回同一结果。"""
    session = get_owned_session(db, user, session_id)

    if not session.is_final:
        finalize(db, session, timed_out=is_timed_out_with_grace(session))
        db.refresh(session)

    settle = settle_rewards_safe(db, session)

    payload = result_view(db, session)
    payload["reward"] = settle
    return payload


def ensure_finalized_if_expired(db: Session, session: QuizSession) -> dict[str, Any] | None:
    """任意接口访问到超时会话时的惰性结算。"""
    if session.is_final:
        return None
    if not is_timed_out(session):
        return None
    finalize(db, session, timed_out=True)
    db.refresh(session)
    return settle_rewards_safe(db, session)


def settle_rewards_safe(db: Session, session: QuizSession) -> dict[str, Any]:
    """奖励结算：失败只降级，不影响成绩。"""
    if not settings.reward_enabled:
        return {"enabled": False, "spEarned": session.sp_earned, "newlyUnlocked": []}
    try:
        from app.services import rewards as reward_service

        data = reward_service.settle_session(db, session)
        data["enabled"] = True
        return data
    except Exception as exc:  # noqa: BLE001  奖励是旁路，必须吞掉异常
        db.rollback()
        return {
            "enabled": True,
            "error": ErrorCode.REWARD_UNAVAILABLE,
            "message": f"奖励结算暂不可用：{exc}",
            "spEarned": session.sp_earned,
            "newlyUnlocked": [],
        }


# ------------------------------------------------------------------ 视图
def session_view(db: Session, session: QuizSession) -> dict[str, Any]:
    """答题页数据：绝不包含标准答案。"""
    questions = sorted(session.session_questions, key=lambda q: q.order_no)
    answered = {
        row.session_question_id: row.user_answer
        for row in db.execute(
            select(QuizAnswer).where(
                QuizAnswer.session_question_id.in_([q.id for q in questions])
            )
        ).scalars()
    }
    now = _now()
    remaining = max(0, int((session.expires_at - now).total_seconds()))
    return {
        "sessionId": session.id,
        "status": session.status,
        "questionCount": session.question_count,
        "perQuestionSeconds": per_question_seconds(),
        "totalSeconds": session.question_count * per_question_seconds(),
        "startsAt": session.starts_at.isoformat(),
        "expiresAt": session.expires_at.isoformat(),
        "serverTime": now.isoformat(),
        "remainingSeconds": remaining,
        "categoryFilter": session.category_filter,
        "difficultyFilter": session.difficulty_filter,
        "questions": [
            {**q.to_public_dict(), "selected": answered.get(q.id)}
            for q in questions
        ],
    }


def result_view(db: Session, session: QuizSession) -> dict[str, Any]:
    """结果页数据：此时可以返回标准答案与解析。"""
    if not session.is_final:
        raise APIError(ErrorCode.SESSION_NOT_FOUND, "本局尚未结算，无法查看结果")

    questions = sorted(session.session_questions, key=lambda q: q.order_no)
    answers = {
        row.session_question_id: row
        for row in db.execute(
            select(QuizAnswer).where(
                QuizAnswer.session_question_id.in_([q.id for q in questions])
            )
        ).scalars()
    }

    detail = []
    for sq in questions:
        record = answers.get(sq.id)
        qtype = sq.qtype_snapshot or QuestionType.SINGLE
        words = sq.keywords()
        user_answer = record.user_answer if record else None
        detail.append({
            "sessionQuestionId": sq.id,
            "orderNo": sq.order_no,
            "qtype": qtype,
            "qtypeLabel": QUESTION_TYPE_LABELS.get(qtype, "单选题"),
            "content": sq.content_snapshot,
            "options": sq.option_map(),
            "correctAnswer": sq.correct_answer_snapshot,
            # 填空题回放命中情况：让「为什么算对/算错」当场可解释
            "keywords": words,
            "matchedKeywords": hit_keywords(user_answer, words) if qtype == QuestionType.BLANK else [],
            "userAnswer": user_answer,
            "isCorrect": bool(
                record
                and answer_is_correct(qtype, sq.correct_answer_snapshot, words, user_answer)
            ),
            "explanation": sq.explanation_snapshot,
            "difficulty": sq.difficulty_snapshot,
            "difficultyLabel": DIFFICULTY_LABELS.get(sq.difficulty_snapshot, "-"),
            "category": sq.category_snapshot,
            "sourceTitle": sq.source_title_snapshot,
            "sourceSection": sq.source_section_snapshot,
        })

    total = session.question_count
    return {
        "sessionId": session.id,
        "status": session.status,
        "timedOut": session.status == SessionStatus.TIMEOUT,
        "score": session.score,
        "correctCount": session.correct_count,
        "wrongCount": session.wrong_count,
        "unansweredCount": session.unanswered_count or 0,
        "questionCount": total,
        "correctRate": round((session.correct_count or 0) / total, 4) if total else 0,
        "durationSeconds": session.duration_seconds,
        "grade": session.grade,
        "gradeLabel": GRADE_LABELS.get(session.grade or "", "-"),
        "ruleVersion": session.rule_version,
        "scoringMode": "ACCURACY",
        "explain": f"正确率口径：{session.correct_count} × 100 ÷ {total} = {session.score}",
        "spEarned": session.sp_earned,
        "questions": detail,
        "finishedAt": session.submitted_at.isoformat() if session.submitted_at else None,
    }


def _dump_breakdown(breakdown: dict[str, Any]) -> str:
    import json

    return json.dumps(breakdown, ensure_ascii=False)
