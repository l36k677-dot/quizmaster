"""统计与排行榜。

口径统一：只统计终态会话（COMPLETED / TIMEOUT），IN_PROGRESS 不计入。
排行榜按用户聚合，避免高频答题产生重复占位。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import QuizSession, Role, SessionStatus, User
from app.services.quiz_service import per_question_seconds
from app.services.scoring import GRADE_LABELS


def _final_filter():
    return QuizSession.status.in_(SessionStatus.FINAL)


# ------------------------------------------------------------------ 排行榜聚合
def aggregate_users(db: Session) -> list[dict[str, Any]]:
    """把全部终态会话按用户聚合（数据量小，内存聚合即可读性优先）。"""
    rows = list(
        db.execute(
            select(QuizSession, User)
            .join(User, User.id == QuizSession.user_id)
            .where(_final_filter())
            .order_by(QuizSession.created_at.asc())
        )
    )
    buckets: dict[str, dict[str, Any]] = {}
    for session, user in rows:
        if user.role == Role.ADMIN and not user.is_demo:
            # 管理员默认不进正式排行榜，避免演示账号干扰名次
            continue
        if user.is_demo and not settings.leaderboard_include_demo:
            continue
        bucket = buckets.setdefault(
            user.id,
            {
                "userId": user.id,
                "username": user.username,
                "displayName": user.display_name or user.username,
                "isDemo": user.is_demo,
                "sessions": 0,
                "bestScore": 0,
                "scoreSum": 0,
                "correctRateSum": 0.0,
                "bestDuration": None,
                "lastPlayedAt": None,
                "studyPoints": user.study_points or 0,
            },
        )
        bucket["sessions"] += 1
        bucket["bestScore"] = max(bucket["bestScore"], session.score or 0)
        bucket["scoreSum"] += session.score or 0
        bucket["correctRateSum"] += (session.correct_count or 0) / session.question_count
        duration = session.duration_seconds
        if duration is not None and (bucket["bestDuration"] is None or duration < bucket["bestDuration"]):
            bucket["bestDuration"] = duration
        moment = session.submitted_at or session.created_at
        if bucket["lastPlayedAt"] is None or moment > bucket["lastPlayedAt"]:
            bucket["lastPlayedAt"] = moment

    items = list(buckets.values())
    for item in items:
        item["avgScore"] = round(item["scoreSum"] / item["sessions"], 1) if item["sessions"] else 0
        item["avgCorrectRate"] = (
            round(item["correctRateSum"] / item["sessions"], 4) if item["sessions"] else 0
        )
    # 排序：最佳分数 → 平均正确率 → 最佳用时（第 3 排序键，稳定）
    items.sort(
        key=lambda i: (
            -i["bestScore"],
            -i["avgCorrectRate"],
            i["bestDuration"] if i["bestDuration"] is not None else 10**9,
            i["username"],
        )
    )
    for index, item in enumerate(items, start=1):
        item["rank"] = index
    return items


def leaderboard(db: Session, user: User, *, limit: int = 20) -> dict[str, Any]:
    items = aggregate_users(db)
    top = items[: max(1, min(100, limit))]
    mine = next((i for i in items if i["userId"] == user.id), None)
    return {
        "items": [_serialize_leader(item) for item in top],
        "myRank": (_serialize_leader(mine) if mine else None),
        "totalPlayers": len(items),
        "sortRule": "最佳分数 ↓ → 平均正确率 ↓ → 最佳用时 ↑",
        "includeDemo": settings.leaderboard_include_demo,
    }


def _serialize_leader(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": item["rank"],
        "userId": item["userId"],
        "username": item["username"],
        "displayName": item["displayName"],
        "isDemo": item["isDemo"],
        "sessions": item["sessions"],
        "bestScore": item["bestScore"],
        "avgScore": item["avgScore"],
        "avgCorrectRate": item["avgCorrectRate"],
        "bestDuration": item["bestDuration"],
        "studyPoints": item["studyPoints"],
    }


# ------------------------------------------------------------------ Dashboard
def dashboard(db: Session, user: User) -> dict[str, Any]:
    sessions = list(
        db.execute(
            select(QuizSession)
            .where(QuizSession.user_id == user.id)
            .where(_final_filter())
            .order_by(QuizSession.created_at.desc())
        ).scalars()
    )
    finished = len(sessions)
    best = max((s.score or 0 for s in sessions), default=0)
    correct_sum = sum(s.correct_count or 0 for s in sessions)
    question_sum = sum(s.question_count for s in sessions)
    avg_rate = round(correct_sum / question_sum, 4) if question_sum else 0

    items = aggregate_users(db)
    mine = next((i for i in items if i["userId"] == user.id), None)

    trend = [
        {
            "sessionId": s.id,
            "score": s.score,
            "status": s.status,
            "createdAt": s.created_at.isoformat(),
            "difficultyFilter": s.difficulty_filter,
        }
        for s in reversed(sessions[:10])
    ]

    recent = [_serialize_history_row(s) for s in sessions[:5]]

    return {
        "user": {
            "username": user.username,
            "displayName": user.display_name or user.username,
            "role": user.role,
        },
        "stats": {
            "finishedSessions": finished,
            "bestScore": best,
            "avgCorrectRate": avg_rate,
            "rank": mine["rank"] if mine else None,
            "totalPlayers": len(items),
        },
        "trend": trend,
        "recent": recent,
        "rewardEnabled": settings.reward_enabled,
    }


# ------------------------------------------------------------------ 历史
def _serialize_history_row(session: QuizSession) -> dict[str, Any]:
    return {
        "sessionId": session.id,
        "status": session.status,
        "score": session.score,
        "correctCount": session.correct_count,
        "wrongCount": session.wrong_count,
        "questionCount": session.question_count,
        "correctRate": (
            round((session.correct_count or 0) / session.question_count, 4)
            if session.question_count else 0
        ),
        "durationSeconds": session.duration_seconds,
        "grade": session.grade,
        "gradeLabel": GRADE_LABELS.get(session.grade or "", "-"),
        "difficultyFilter": session.difficulty_filter,
        "categoryFilter": session.category_filter,
        "spEarned": session.sp_earned,
        "createdAt": session.created_at.isoformat(),
        "finishedAt": session.submitted_at.isoformat() if session.submitted_at else None,
    }


def history(
    db: Session,
    user: User,
    *,
    page: int = 1,
    page_size: int = 10,
    status: str | None = None,
) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = max(1, min(50, int(page_size)))

    conditions = [QuizSession.user_id == user.id, _final_filter()]
    if status in SessionStatus.FINAL:
        conditions.append(QuizSession.status == status)

    total = int(
        db.execute(
            select(func.count(QuizSession.id)).where(*conditions)
        ).scalar_one()
    )
    rows = list(
        db.execute(
            select(QuizSession)
            .where(*conditions)
            .order_by(QuizSession.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return {
        "items": [_serialize_history_row(s) for s in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size),
    }


# ------------------------------------------------------------------ 管理端统计
def admin_overview(db: Session) -> dict[str, Any]:
    total_users = int(db.execute(select(func.count(User.id))).scalar_one())
    total_sessions = int(
        db.execute(select(func.count(QuizSession.id)).where(_final_filter())).scalar_one()
    )
    avg_score = db.execute(
        select(func.avg(QuizSession.score)).where(_final_filter())
    ).scalar_one()
    return {
        "totalUsers": total_users,
        "finishedSessions": total_sessions,
        "averageScore": round(float(avg_score), 1) if avg_score is not None else 0,
        "perQuestionSeconds": per_question_seconds(),
    }
