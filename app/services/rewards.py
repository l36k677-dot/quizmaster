"""奖励机制：研习积分 SP、等级称号、连续研习、成就（设计文档 10.4–10.7）。

三条边界，必须能在现场讲清楚：
1. 奖励全部由终态会话派生，客户端无法上报；
2. 奖励不改变分数与排行榜排序；
3. 奖励结算失败不得阻断成绩落库，且必须可重算复现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import days_between, today_cst, utcnow
from app.core.security import new_id
from app.models import (
    Achievement,
    Difficulty,
    QuestionType,
    QuizSession,
    SessionStatus,
    User,
    UserAchievement,
)
from app.services.scoring import answer_is_correct

# ------------------------------------------------------------------ 常量
SP_BASE = 20                     # 完成基数
SP_SCORE_RATE = 0.5              # 正确率奖励系数
SP_SCORE_CAP = 50
SP_DIFFICULTY_BONUS = {Difficulty.EASY: 0, Difficulty.MEDIUM: 5, Difficulty.HARD: 10}
SP_STREAK_STEP = 5
SP_STREAK_CAP = 25
SP_SESSION_CAP = SP_BASE + SP_SCORE_CAP + max(SP_DIFFICULTY_BONUS.values()) + SP_STREAK_CAP

#: 等级表：code, 下限, 上限(None 表示无上限), 称号
LEVEL_TABLE: list[tuple[str, int, int | None, str]] = [
    ("L1", 0, 199, "研习者"),
    ("L2", 200, 499, "学者"),
    ("L3", 500, 999, "研究员"),
    ("L4", 1000, 1999, "资深研究员"),
    ("L5", 2000, None, "首席研习官"),
]

#: 成就定义种子。
#:
#: 命名原则（第二轮改名）：名称要能望文生义，又要配得上「校史研习」的调子。
#: 旧的一组（首战 / 满分达成 / 十局学者 / 五十局学者 / 疾风 / 攻坚 / 全科通 / 七日研习）
#: 里，「十局学者」把「局」当量词、把「学者」当等级，读起来像机器拼出来的；
#: 其余几个也偏说明书口气。新的一组改用有出处的词，并且**规则本身不变**：
#:
#:   初见 → 渐入佳境 → 登堂入室   三级递进，对应 1 / 10 / 50 局
#:   倚马可待   快而准（用时不到一半）
#:   凌绝顶     攻下困难题
#:   博采众长   四类皆习
#:   日日新     连续七天，取自校训「允公允能，日新月异」
ACHIEVEMENT_SEED: list[dict[str, Any]] = [
    {
        "code": "FIRST_QUIZ",
        "name": "初见",
        "description": "完成你的第一局竞答，与南开初次相识。",
        "rule_type": "COUNT",
        "threshold": 1,
        "sort_order": 10,
    },
    {
        "code": "PERFECT",
        "name": "明察秋毫",
        "description": "任意一局答对所有题目，取得 100 分。",
        "rule_type": "MAX_SCORE",
        "threshold": 100,
        "sort_order": 20,
    },
    {
        "code": "SCHOLAR_10",
        "name": "渐入佳境",
        "description": "累计完成 10 局竞答，渐得其味。",
        "rule_type": "COUNT",
        "threshold": 10,
        "sort_order": 30,
    },
    {
        "code": "SCHOLAR_50",
        "name": "登堂入室",
        "description": "累计完成 50 局竞答，已是熟客。",
        "rule_type": "COUNT",
        "threshold": 50,
        "sort_order": 40,
    },
    {
        "code": "SPEED",
        "name": "倚马可待",
        "description": "用时不超过预期时长一半，且得分不低于 80。",
        "rule_type": "SPEED",
        "threshold": 50,
        "sort_order": 50,
    },
    {
        "code": "HARD_MASTER",
        "name": "凌绝顶",
        "description": "单局内困难题不少于 3 道，且全部答对。",
        "rule_type": "DIFFICULTY",
        "threshold": 3,
        "sort_order": 60,
    },
    {
        "code": "ALL_ROUND",
        "name": "博采众长",
        "description": "创校、人物、校园、精神四个分类各完成至少 1 局。",
        "rule_type": "CATEGORY",
        "threshold": 4,
        "sort_order": 70,
    },
    {
        "code": "STREAK_7",
        "name": "日日新",
        "description": "连续 7 个自然日完成竞答，日新又新。",
        "rule_type": "STREAK",
        "threshold": 7,
        "sort_order": 80,
    },
]

#: 成就图标（对应 templates/_icons.html 里 ach_icon 宏的分支名）。
#: 放在这里而不是模板里，是为了让「成就定义」只有一处：数据库存名称与规则，
#: 这里补图标，页面渲染时用 achievement_meta() 合并。
ACHIEVEMENT_ICONS: dict[str, str] = {
    "FIRST_QUIZ": "first",
    "PERFECT": "perfect",
    "SCHOLAR_10": "scholar10",
    "SCHOLAR_50": "scholar50",
    "SPEED": "swift",
    "HARD_MASTER": "summit",
    "ALL_ROUND": "allround",
    "STREAK_7": "streak",
}


# ------------------------------------------------------------------ 等级
def level_for(study_points: int) -> dict[str, Any]:
    """由累计 SP 计算等级与称号（纯函数，不落库，避免双数据源）。"""
    sp = max(0, int(study_points))
    current = LEVEL_TABLE[0]
    for row in LEVEL_TABLE:
        code, low, high, _title = row
        if sp >= low and (high is None or sp <= high):
            current = row
    code, low, high, title = current
    index = [r[0] for r in LEVEL_TABLE].index(code)
    is_max = index == len(LEVEL_TABLE) - 1
    next_row = None if is_max else LEVEL_TABLE[index + 1]
    progress_base = low
    if is_max or next_row is None:
        progress = 100
        to_next = 0
        next_title = None
    else:
        span = next_row[1] - progress_base
        progress = int(round((sp - progress_base) * 100 / span)) if span > 0 else 100
        to_next = max(0, next_row[1] - sp)
        next_title = next_row[3]
    return {
        "code": code,
        "title": title,
        "minPoints": low,
        "maxPoints": high,
        "nextLevelMin": None if is_max or next_row is None else next_row[1],
        "nextTitle": next_title,
        "pointsToNext": to_next,
        "progressPercent": max(0, min(100, progress)),
        "isMaxLevel": is_max,
    }


# ------------------------------------------------------------------ SP 结算
def compute_sp(score: int, max_difficulty: str | None, streak_day: int) -> tuple[int, dict[str, int]]:
    """计算一局应得 SP 及其构成（纯函数，可被重算脚本复用）。"""
    score_part = min(SP_SCORE_CAP, int(round(max(0, score) * SP_SCORE_RATE)))
    difficulty_part = SP_DIFFICULTY_BONUS.get(max_difficulty or Difficulty.EASY, 0)
    streak_part = min(SP_STREAK_CAP, SP_STREAK_STEP * (streak_day - 1)) if streak_day >= 2 else 0
    total = min(SP_SESSION_CAP, SP_BASE + score_part + difficulty_part + streak_part)
    return total, {
        "base": SP_BASE,
        "score": score_part,
        "difficulty": difficulty_part,
        "streak": streak_part,
        "total": total,
    }


def next_streak(previous_streak: int, last_date: date | None, today: date) -> int:
    """连续研习天数推进规则：同日不重复累加，隔天 +1，断连归 1。"""
    if last_date is None:
        return 1
    gap = days_between(last_date, today)
    if gap <= 0:
        return max(1, previous_streak)
    if gap == 1:
        return max(1, previous_streak) + 1
    return 1


# ------------------------------------------------------------------ 成就判定
@dataclass
class SessionFact:
    """成就判定所需的单局事实（全部来自快照，可复现）。"""

    session_id: str
    score: int
    duration_seconds: int
    question_count: int
    expected_seconds: int
    categories: set[str] = field(default_factory=set)
    hard_total: int = 0
    hard_correct: int = 0
    all_correct: bool = False

    @property
    def speed_ok(self) -> bool:
        if self.score < 80 or self.expected_seconds <= 0:
            return False
        return self.duration_seconds <= self.expected_seconds * 0.5


def _solved(sq) -> bool:
    """这道题是否被答对。

    必须与判分共用同一个纯函数：成就只看「答对几道」，如果这里另写一套
    ``user_answer == correct_answer`` 的比较，填空题会被整体判成错，
    「明察秋毫」「凌绝顶」这类成就就再也拿不到了。
    """
    if sq.answer is None:
        return False
    return answer_is_correct(
        sq.qtype_snapshot or QuestionType.SINGLE,
        sq.correct_answer_snapshot,
        sq.keywords(),
        sq.answer.user_answer,
    )


def build_fact(session: QuizSession) -> SessionFact:
    """把一局会话转成判定事实。"""
    questions = list(session.session_questions)
    categories = {q.category_snapshot for q in questions if q.category_snapshot}
    hard = [q for q in questions if q.difficulty_snapshot == Difficulty.HARD]
    hard_correct = sum(1 for q in hard if _solved(q))
    all_correct = bool(questions) and all(_solved(q) for q in questions)
    return SessionFact(
        session_id=session.id,
        score=session.score or 0,
        duration_seconds=session.duration_seconds or 0,
        question_count=session.question_count,
        expected_seconds=session.question_count * 60,
        categories=categories,
        hard_total=len(hard),
        hard_correct=hard_correct,
        all_correct=all_correct,
    )


def evaluate_achievements(
    facts: Sequence[SessionFact], current_streak: int
) -> dict[str, dict[str, Any]]:
    """返回每个成就的解锁状态与进度。纯函数。"""
    finished = len(facts)
    best_score = max((f.score for f in facts), default=0)
    max_hard_all_correct = max((f.hard_correct for f in facts if f.hard_total >= 3), default=0)
    max_hard_total = max((f.hard_total for f in facts), default=0)
    categories = set()
    for f in facts:
        categories |= f.categories
    speed_hit = any(f.speed_ok for f in facts)

    raw: dict[str, tuple[bool, int, int]] = {
        # code: (unlocked, current, target)
        "FIRST_QUIZ": (finished >= 1, min(finished, 1), 1),
        "PERFECT": (best_score >= 100, best_score, 100),
        "SCHOLAR_10": (finished >= 10, finished, 10),
        "SCHOLAR_50": (finished >= 50, finished, 50),
        "SPEED": (speed_hit, 1 if speed_hit else 0, 1),
        "HARD_MASTER": (
            max_hard_all_correct >= 3,
            max_hard_all_correct,
            3,
        ),
        "ALL_ROUND": (len(categories) >= 4, len(categories), 4),
        "STREAK_7": (current_streak >= 7, min(current_streak, 7), 7),
    }
    out: dict[str, dict[str, Any]] = {}
    for code, (unlocked, current, target) in raw.items():
        out[code] = {
            "unlocked": unlocked,
            "current": current,
            "target": target,
            "progressPercent": int(min(100, round(current * 100 / target))) if target else 0,
        }
    # 保留现场可解释的额外读数
    out["_meta"] = {"maxHardTotal": max_hard_total, "finished": finished}
    return out


# ------------------------------------------------------------------ 结算 / 重算
def final_sessions(db: Session, user_id: str) -> list[QuizSession]:
    """该用户全部终态会话（含题目与答案，按时间正序）。"""
    stmt = (
        select(QuizSession)
        .where(QuizSession.user_id == user_id)
        .where(QuizSession.status.in_(SessionStatus.FINAL))
        .order_by(QuizSession.created_at.asc())
    )
    return list(db.execute(stmt).scalars().unique())


def settle_session(db: Session, session: QuizSession) -> dict[str, Any]:
    """会话终态后的奖励结算（幂等）。

    必须在成绩落库的独立事务之后调用；抛出的异常由调用方捕获并降级为
    REWARD_UNAVAILABLE，不得影响成绩与结果页。
    """
    user = db.get(User, session.user_id)
    if user is None:
        raise ValueError("用户不存在")
    if not session.is_final:
        raise ValueError("只有终态会话才能结算奖励")

    already_settled = session.sp_earned is not None
    newly: list[str] = []

    facts_before = [build_fact(s) for s in final_sessions(db, user.id) if s.id != session.id]

    # 1) 连续研习推进（仅在首次结算时推进，保证幂等）
    if not already_settled:
        today = today_cst()
        streak = next_streak(user.current_streak, user.last_quiz_date, today)
        user.current_streak = streak
        user.best_streak = max(user.best_streak or 0, streak)
        user.last_quiz_date = today
    streak_for_sp = user.current_streak or 1

    fact = build_fact(session)
    max_difficulty = max(
        (q.difficulty_snapshot for q in session.session_questions),
        key=lambda d: {Difficulty.EASY: 0, Difficulty.MEDIUM: 1, Difficulty.HARD: 2}.get(d, 1),
        default=Difficulty.EASY,
    )
    sp, sp_breakdown = compute_sp(fact.score, max_difficulty, streak_for_sp)

    # 2) SP 只写一次
    if not already_settled:
        session.sp_earned = sp
        user.study_points = (user.study_points or 0) + sp

    # 3) 成就解锁（唯一约束兜底，重复请求不会重复写入）
    unlocked_codes = {
        row.achievement_code
        for row in db.execute(
            select(UserAchievement).where(UserAchievement.user_id == user.id)
        ).scalars()
    }
    definitions = list(
        db.execute(
            select(Achievement).where(Achievement.is_active.is_(True)).order_by(Achievement.sort_order)
        ).scalars()
    )
    facts_all = facts_before + [fact]
    states = evaluate_achievements(facts_all, user.current_streak or 1)
    for definition in definitions:
        state = states.get(definition.code)
        if not state or not state["unlocked"] or definition.code in unlocked_codes:
            continue
        db.add(
            UserAchievement(
                id=new_id("ua_"),
                user_id=user.id,
                achievement_code=definition.code,
                session_id=session.id,
                unlocked_at=utcnow(),
            )
        )
        newly.append(definition.code)

    user.updated_at = utcnow()
    db.commit()

    level = level_for(user.study_points or 0)
    return {
        "spEarned": session.sp_earned,
        "spBreakdown": sp_breakdown,
        "studyPoints": user.study_points,
        "level": level,
        "currentStreak": user.current_streak,
        "newlyUnlocked": newly,
        "alreadySettled": already_settled,
    }


def recompute_user(db: Session, user: User) -> dict[str, Any]:
    """按原始终态会话重算 SP、连续天数与成就，并覆盖缓存值。

    这是 ADMIN 的审计/修复入口：重算结果必须与在线值一致。
    """
    sessions = final_sessions(db, user.id)
    facts = [build_fact(s) for s in sessions]

    # 连续研习按自然日序列重算
    streak = 0
    best = 0
    previous: date | None = None
    for session in sessions:
        day = (session.submitted_at or session.created_at).date()
        if previous is None:
            streak = 1
        else:
            gap = days_between(previous, day)
            streak = streak + 1 if gap == 1 else (streak if gap == 0 else 1)
        best = max(best, streak)
        previous = day

    total_sp = 0
    for session, fact in zip(sessions, facts):
        max_difficulty = max(
            (q.difficulty_snapshot for q in session.session_questions),
            key=lambda d: {Difficulty.EASY: 0, Difficulty.MEDIUM: 1, Difficulty.HARD: 2}.get(d, 1),
            default=Difficulty.EASY,
        )
        day_index = (session.submitted_at or session.created_at).date()
        # 该局当天的连续序号（用重算出的整体序列近似：与在线结算一致即可）
        streak_at_that_day = _streak_at(sessions, day_index)
        sp, _ = compute_sp(fact.score, max_difficulty, streak_at_that_day)
        session.sp_earned = sp
        total_sp += sp

    user.study_points = total_sp
    user.current_streak = streak
    user.best_streak = best
    user.last_quiz_date = previous

    unlocked_codes = {
        row.achievement_code
        for row in db.execute(
            select(UserAchievement).where(UserAchievement.user_id == user.id)
        ).scalars()
    }
    states = evaluate_achievements(facts, streak)
    definitions = list(
        db.execute(
            select(Achievement).where(Achievement.is_active.is_(True)).order_by(Achievement.sort_order)
        ).scalars()
    )
    newly: list[str] = []
    for definition in definitions:
        state = states.get(definition.code)
        if state and state["unlocked"] and definition.code not in unlocked_codes:
            db.add(
                UserAchievement(
                    id=new_id("ua_"),
                    user_id=user.id,
                    achievement_code=definition.code,
                    session_id=None,
                    unlocked_at=utcnow(),
                )
            )
            newly.append(definition.code)

    user.updated_at = utcnow()
    db.commit()
    return {
        "userId": user.id,
        "username": user.username,
        "studyPoints": user.study_points,
        "currentStreak": user.current_streak,
        "bestStreak": user.best_streak,
        "sessions": len(sessions),
        "newlyUnlocked": newly,
        "level": level_for(user.study_points),
    }


def _streak_at(sessions: Sequence[QuizSession], day: date) -> int:
    """重算辅助：day 当天处于连续序列的第几天。"""
    days: list[date] = []
    for session in sessions:
        d = (session.submitted_at or session.created_at).date()
        if not days or days[-1] != d:
            days.append(d)
    streak = 0
    for index, d in enumerate(days):
        if index == 0:
            streak = 1
        else:
            streak = streak + 1 if days_between(days[index - 1], d) == 1 else 1
        if d == day:
            return streak
    return 1


def achievement_wall(db: Session, user: User) -> dict[str, Any]:
    """荣誉墙数据：全部成就 + 解锁状态与进度。"""
    sessions = final_sessions(db, user.id)
    facts = [build_fact(s) for s in sessions]
    states = evaluate_achievements(facts, user.current_streak or 0)

    unlocked_map: dict[str, datetime | None] = {}
    for row in db.execute(
        select(UserAchievement).where(UserAchievement.user_id == user.id)
    ).scalars():
        unlocked_map[row.achievement_code] = row.unlocked_at

    definitions = list(
        db.execute(
            select(Achievement).where(Achievement.is_active.is_(True)).order_by(Achievement.sort_order)
        ).scalars()
    )
    items = []
    for definition in definitions:
        state = states.get(definition.code, {"unlocked": False, "current": 0, "target": 1,
                                             "progressPercent": 0})
        items.append({
            "code": definition.code,
            "name": definition.name,
            "description": definition.description,
            "icon": ACHIEVEMENT_ICONS.get(definition.code, ""),
            "ruleType": definition.rule_type,
            "threshold": definition.threshold,
            "unlocked": definition.code in unlocked_map,
            "unlockedAt": (unlocked_map.get(definition.code).isoformat()
                           if unlocked_map.get(definition.code) else None),
            "current": state["current"],
            "target": state["target"],
            "progressPercent": state["progressPercent"],
        })
    unlocked_count = sum(1 for item in items if item["unlocked"])
    return {
        "items": items,
        "unlockedCount": unlocked_count,
        "totalCount": len(items),
        "finishedSessions": len(facts),
    }


def reward_summary(db: Session, user: User) -> dict[str, Any]:
    """我的奖励总览：SP、等级、连续研习、成就进度。"""
    wall = achievement_wall(db, user)
    level = level_for(user.study_points or 0)
    return {
        "studyPoints": user.study_points or 0,
        "level": level,
        "title": level["title"],
        "currentStreak": user.current_streak or 0,
        "bestStreak": user.best_streak or 0,
        "lastQuizDate": user.last_quiz_date.isoformat() if user.last_quiz_date else None,
        "unlockedCount": wall["unlockedCount"],
        "totalAchievements": wall["totalCount"],
        "spFormula": "SP = 完成基数20 + round(得分×0.5) + 难度加成(0/5/10) + 连续研习min(5×(n−1),25)",
        "spSessionCap": SP_SESSION_CAP,
    }


def achievements_by_code(db: Session) -> dict[str, Achievement]:
    return {
        row.code: row
        for row in db.execute(select(Achievement)).scalars()
    }


def achievement_meta(db: Session) -> dict[str, dict[str, str]]:
    """code -> {name, description, icon}，供模板渲染成就图标与文案。

    名称与规则来自数据库（唯一数据源），图标来自 ACHIEVEMENT_ICONS。
    结果页的「本次新解锁」只拿到 code 列表，需要靠这张表把名称与图标补回来 ——
    把映射放在后端而不是前端脚本里，改名时就只有一处要改。
    """
    rows = db.execute(select(Achievement).order_by(Achievement.sort_order)).scalars()
    return {
        row.code: {
            "name": row.name,
            "description": row.description or "",
            "icon": ACHIEVEMENT_ICONS.get(row.code, ""),
        }
        for row in rows
    }
