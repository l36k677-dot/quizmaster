"""奖励机制测试：SP、等级、连续研习与成就。"""

from __future__ import annotations

from datetime import date, timedelta

from app.services.rewards import (
    SessionFact,
    build_fact,
    compute_sp,
    evaluate_achievements,
    level_for,
    next_streak,
)


def test_level_table_boundaries():
    assert level_for(0)["title"] == "研习者"
    assert level_for(199)["code"] == "L1"
    assert level_for(200)["code"] == "L2"
    assert level_for(499)["code"] == "L2"
    assert level_for(500)["code"] == "L3"
    assert level_for(1000)["code"] == "L4"
    assert level_for(2000)["code"] == "L5"
    assert level_for(99999)["isMaxLevel"] is True


def test_level_progress_points_to_next():
    info = level_for(350)
    assert info["code"] == "L2"
    assert info["nextLevelMin"] == 500
    assert info["pointsToNext"] == 150
    assert 0 <= info["progressPercent"] <= 100


def test_sp_formula_matches_documentation():
    """示例：连续第 3 天、中等难度 10 题得 80 分 → 20 + 40 + 5 + 10 = 75 SP。"""
    sp, parts = compute_sp(80, "MEDIUM", 3)
    assert parts == {"base": 20, "score": 40, "difficulty": 5, "streak": 10, "total": 75}
    assert sp == 75


def test_sp_caps_are_enforced():
    sp, parts = compute_sp(100, "HARD", 20)
    assert parts["score"] == 50          # 上限 50
    assert parts["streak"] == 25         # 上限 25
    assert parts["difficulty"] == 10
    assert sp == 105                     # 单局上限


def test_streak_progression():
    today = date(2026, 9, 20)
    assert next_streak(0, None, today) == 1                       # 首局
    assert next_streak(3, today, today) == 3                      # 同日不重复累加
    assert next_streak(3, today - timedelta(days=1), today) == 4  # 连续 +1
    assert next_streak(9, today - timedelta(days=3), today) == 1  # 断连归 1


def _fact(session_id: str, score: int = 80, duration: int = 300,
          categories=("创校与发展",), hard_total: int = 0, hard_correct: int = 0) -> SessionFact:
    return SessionFact(
        session_id=session_id,
        score=score,
        duration_seconds=duration,
        question_count=10,
        expected_seconds=600,
        categories=set(categories),
        hard_total=hard_total,
        hard_correct=hard_correct,
    )


def test_first_and_count_achievements():
    states = evaluate_achievements([_fact("s1")], current_streak=1)
    assert states["FIRST_QUIZ"]["unlocked"] is True
    assert states["SCHOLAR_10"]["unlocked"] is False
    assert states["SCHOLAR_10"]["current"] == 1


def test_perfect_and_speed_and_hard_master():
    fast = _fact("s1", score=90, duration=200)                       # 用时 ≤ 一半且 ≥ 80
    assert evaluate_achievements([fast], 1)["SPEED"]["unlocked"] is True

    perfect = _fact("s2", score=100, duration=590)
    assert evaluate_achievements([perfect], 1)["PERFECT"]["unlocked"] is True

    hard = _fact("s3", hard_total=3, hard_correct=3)
    assert evaluate_achievements([hard], 1)["HARD_MASTER"]["unlocked"] is True

    hard_fail = _fact("s4", hard_total=3, hard_correct=2)
    assert evaluate_achievements([hard_fail], 1)["HARD_MASTER"]["unlocked"] is False


def test_speed_requires_min_score():
    slow_but_ok = _fact("s1", score=90, duration=301)
    assert evaluate_achievements([slow_but_ok], 1)["SPEED"]["unlocked"] is False
    low_score = _fact("s2", score=70, duration=120)
    assert evaluate_achievements([low_score], 1)["SPEED"]["unlocked"] is False


def test_all_round_requires_four_categories():
    facts = [
        _fact("s1", categories=("创校与发展",)),
        _fact("s2", categories=("人物与校友",)),
        _fact("s3", categories=("校园与文化",)),
    ]
    assert evaluate_achievements(facts, 1)["ALL_ROUND"]["unlocked"] is False
    facts.append(_fact("s4", categories=("精神与学科",)))
    assert evaluate_achievements(facts, 1)["ALL_ROUND"]["unlocked"] is True


def test_streak_seven():
    assert evaluate_achievements([_fact("s1")], 6)["STREAK_7"]["unlocked"] is False
    assert evaluate_achievements([_fact("s1")], 7)["STREAK_7"]["unlocked"] is True
