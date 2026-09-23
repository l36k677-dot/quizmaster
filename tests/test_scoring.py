"""判分口径测试（P0）。"""

from __future__ import annotations

from app.services.scoring import (
    BLANK_MAX_LEN,
    QuestionResult,
    absurd_reason,
    answer_is_correct,
    evaluate,
    grade_for,
    has_profanity,
    hit_keywords,
    is_answered,
    judge_blank,
    normalize_text,
    score_for,
)


def _result(order_no: int, correct: str, answered: str | None, difficulty: str = "MEDIUM"):
    return QuestionResult(
        order_no=order_no, difficulty=difficulty, correct_answer=correct, user_answer=answered
    )


def _blank(order_no: int, answered: str | None, keywords=("1919",), difficulty: str = "MEDIUM"):
    return QuestionResult(
        order_no=order_no,
        difficulty=difficulty,
        correct_answer="1919 年",
        user_answer=answered,
        qtype="BLANK",
        keywords=tuple(keywords),
    )


def test_accuracy_score_is_percentage():
    """10 题答对 8 题 → 80 分。"""
    results = [_result(i, "A", "A" if i <= 8 else "B") for i in range(1, 11)]
    outcome = evaluate(results)
    assert outcome.score == 80
    assert outcome.correct_count == 8
    assert outcome.wrong_count == 2
    assert outcome.unanswered_count == 0
    assert outcome.grade == "PASS"


def test_unanswered_counts_as_wrong():
    """未作答计入分母、不计入分子。"""
    results = [_result(1, "A", "A"), _result(2, "B", None), _result(3, "C", None), _result(4, "D", "D")]
    outcome = evaluate(results)
    assert outcome.correct_count == 2
    assert outcome.unanswered_count == 2
    assert outcome.wrong_count == 2
    assert outcome.score == 50


def test_score_never_exceeds_boundaries():
    assert score_for(10, 10) == 100
    assert score_for(0, 10) == 0
    assert score_for(1, 3) == 33
    assert score_for(0, 0) == 0


def test_grade_thresholds():
    assert grade_for(100) == "EXCELLENT"
    assert grade_for(99) == "GOOD"
    assert grade_for(85) == "GOOD"
    assert grade_for(84) == "PASS"
    assert grade_for(60) == "PASS"
    assert grade_for(59) == "NEEDS_WORK"
    assert grade_for(0) == "NEEDS_WORK"


def test_weighted_mode_uses_difficulty_weights():
    """口径 B：答对 1 道困难题应高于答对 1 道简单题。"""
    hard_only = evaluate([_result(1, "A", "A", "HARD"), _result(2, "A", "B", "EASY")],
                         mode="WEIGHTED", rule_version="B-1")
    easy_only = evaluate([_result(1, "A", "A", "EASY"), _result(2, "A", "B", "HARD")],
                         mode="WEIGHTED", rule_version="B-1")
    assert hard_only.score > easy_only.score
    assert hard_only.rule_version == "B-1"


def test_breakdown_is_reproducible():
    """逐题拆解必须能复原分数，便于现场纸笔验算与审计。"""
    results = [
        _result(1, "A", "A", "EASY"),
        _result(2, "B", "C", "HARD"),
        _result(3, "C", None, "MEDIUM"),
    ]
    outcome = evaluate(results)
    breakdown = outcome.breakdown
    assert breakdown["questionCount"] == 3
    assert breakdown["correctCount"] == 1
    assert breakdown["perQuestion"][1]["answered"] == "C"
    assert breakdown["perQuestion"][1]["isCorrect"] is False
    assert breakdown["perQuestion"][2]["answered"] is None
    assert outcome.score == round(breakdown["correctCount"] * 100 / breakdown["questionCount"])


# ==================================================================
# 判断题：复用 A/B 键位，判分与单选同构
# ==================================================================
def test_judge_uses_ab_keys():
    assert answer_is_correct("JUDGE", "A", (), "A") is True
    assert answer_is_correct("JUDGE", "A", (), "a") is True      # 大小写不敏感
    assert answer_is_correct("JUDGE", "A", (), "B") is False
    # 判断题为「错误」时，答「正确」是错的
    assert answer_is_correct("JUDGE", "B", (), "A") is False


def test_judge_cannot_be_answered_with_cd():
    assert answer_is_correct("JUDGE", "A", (), "C") is False
    assert is_answered("JUDGE", "C") is False


# ==================================================================
# 填空题：判分口径 —— 答出关键字、无污言秽语、不明显离谱
# ==================================================================
def test_normalize_text_folds_noise():
    assert normalize_text(" １９１９ 年。 ") == "1919年"
    assert normalize_text("一九一九年") == "1919年"      # 中文数字也能对上年份题
    assert normalize_text("允公允能，日新月异") == "允公允能日新月异"
    assert normalize_text(None) == ""


def test_blank_accepts_keyword_hit():
    """只要命中关键字就算答对，措辞不必与参考答案一致。"""
    for answer in ("1919", "1919 年", "是 1919 年吧", "一九一九年", "大概1919年左右"):
        ok, reason = judge_blank(answer, ["1919"])
        assert ok is True, (answer, reason)
        assert "1919" in reason


def test_blank_accepts_any_of_several_keywords():
    """多关键字取「命中任意一个」—— 校训题答出前半句也算对。"""
    ok, _ = judge_blank("允公允能", ["允公允能", "日新月异"])
    assert ok is True
    ok, _ = judge_blank("日新月异", ["允公允能", "日新月异"])
    assert ok is True
    ok, _ = judge_blank("自强不息", ["允公允能", "日新月异"])
    assert ok is False


def test_blank_rejects_missing_keyword():
    ok, reason = judge_blank("不知道", ["1919"])
    assert ok is False
    assert "1919" in reason          # 提示里要带上应有的要点


def test_blank_empty_is_unanswered_not_wrong_answer():
    ok, reason = judge_blank("   ", ["1919"])
    assert ok is False
    assert reason == "未作答"
    assert is_answered("BLANK", "   ") is False
    assert is_answered("BLANK", "1919") is True


def test_blank_rejects_profanity_even_with_keyword():
    """带脏话一律判错，哪怕关键字答对了。"""
    assert has_profanity("你这个傻逼") is True
    assert has_profanity("1919") is False
    ok, reason = judge_blank("你是傻逼吗 1919", ["1919"])
    assert ok is False
    assert reason == "含不当用语"
    # 标点/大小写不算绕过手段
    ok, _ = judge_blank("F U C K 1919", ["1919"])
    assert ok is False


def test_blank_rejects_absurd_input():
    assert absurd_reason("啊啊啊啊啊啊啊") != ""
    assert absurd_reason("x" * (BLANK_MAX_LEN + 1)) != ""
    assert absurd_reason("。，、！？") != ""
    assert absurd_reason("1919") == ""
    ok, _ = judge_blank("长" * (BLANK_MAX_LEN + 1), ["长"])
    assert ok is False


def test_blank_falls_back_to_reference_answer():
    """出题人只配了参考答案、关键字写得不准时，完全一致仍算对。"""
    ok, reason = judge_blank("1919 年", [], reference="1919 年")
    assert ok is True
    assert "参考答案" in reason
    ok, _ = judge_blank("1919 年？", [], reference="1919 年")
    assert ok is True          # 归一化去掉标点后一致


def test_hit_keywords_reports_which_ones():
    assert hit_keywords("允公允能，日新月异", ["允公允能", "日新月异", "自强不息"]) == [
        "允公允能", "日新月异",
    ]
    assert hit_keywords("不知道", ["1919"]) == []


def test_evaluate_mixes_all_three_types():
    """一局里三种题型混排时，正确数与分数必须同时成立。"""
    results = [
        _result(1, "A", "A"),                    # 单选：对
        QuestionResult(order_no=2, difficulty="MEDIUM", correct_answer="A",
                       user_answer="A", qtype="JUDGE"),          # 判断：对
        _blank(3, "1919 年"),                    # 填空：对（命中关键字）
        _blank(4, "不知道"),                      # 填空：错
        _blank(5, None),                         # 填空：未作答
    ]
    outcome = evaluate(results)
    assert outcome.correct_count == 3
    assert outcome.wrong_count == 2
    assert outcome.unanswered_count == 1
    assert outcome.score == 60
    assert [row["qtype"] for row in outcome.breakdown["perQuestion"]] == [
        "SINGLE", "JUDGE", "BLANK", "BLANK", "BLANK",
    ]
    assert outcome.breakdown["perQuestion"][2]["isCorrect"] is True
    assert outcome.breakdown["perQuestion"][4]["answered"] is None
