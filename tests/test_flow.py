"""核心闭环端到端测试：认证 → 抽题 → 作答 → 判分 → 统计 → 权限 → 超时。

这些用例对应设计文档 22.2 的核心测试用例，是 P0 验收的自动化证据。
"""

from __future__ import annotations

from datetime import timedelta

from app.core.clock import utcnow


def _payload(response):
    body = response.json()
    assert body["success"] is True, body
    return body["data"]


def _start_session(client, count: int = 5, **filters):
    response = client.post(
        "/api/quiz/sessions",
        json={"questionCount": count, **filters},
    )
    assert response.status_code == 200, response.text
    return _payload(response)


def _pick(session, qtype: str):
    """从一局里取出指定题型的题。

    抽题是随机的，所以「随便挑第一题去发一个字母选项」在引入判断题和
    填空题之后就不再成立了 —— 判断题只接受 A/B，填空题接受任意文本。
    需要断言键位行为的用例必须显式挑单选。
    """
    found = next((q for q in session["questions"] if q["qtype"] == qtype), None)
    assert found is not None, f"本局没有 {qtype} 题目：{[q['qtype'] for q in session['questions']]}"
    return found


def _typed_session(client, count: int = 20):
    """抽满「题型测试」分类：确定性地拿到全部 10 道填空题 + 10 道判断题。"""
    return _start_session(client, count, categoryId="cat_types")


# ------------------------------------------------------------------ 认证
def test_requires_login(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_login_rejects_wrong_password(client):
    response = client.post("/api/auth/login", json={"username": "test", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_sets_httponly_cookie(client):
    response = client.post("/api/auth/login", json={"username": "test", "password": "test36"})
    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "")
    assert "qm_session=" in cookie_header
    assert "HttpOnly" in cookie_header


def test_me_returns_current_user(student_client):
    data = _payload(student_client.get("/api/auth/me"))
    assert data["username"] == "test"
    assert data["role"] == "STUDENT"


# ------------------------------------------------------------------ 抽题与快照
def test_answer_payload_never_leaks_correct_answer(student_client):
    """核心不可变规则 6：标准答案在会话终结前不能返回客户端。

    按题型分别校验：单选必须给 A–D 四个选项；判断题只有两个；
    填空题一个选项都没有，而且绝不能泄露判分关键字。
    """
    session = _start_session(student_client, 5)
    assert len(session["questions"]) == 5
    for question in session["questions"]:
        assert "correctAnswer" not in question
        assert "correct_answer" not in question
        assert "keywords" not in question          # 填空题的判分关键字同样不能给前端
        assert "keywords_json" not in question
        qtype = question["qtype"]
        option_keys = set(question["options"].keys())
        if qtype == "SINGLE":
            assert option_keys == {"A", "B", "C", "D"}
        elif qtype == "JUDGE":
            assert option_keys == {"A", "B"}
            assert question["options"]["A"] == "正确"
            assert question["options"]["B"] == "错误"
        else:
            assert qtype == "BLANK"
            assert option_keys == set()


def test_questions_are_unique_in_one_session(student_client):
    session = _start_session(student_client, 10)
    ids = [q["sessionQuestionId"] for q in session["questions"]]
    assert len(set(ids)) == 10


def test_question_not_enough_raises(student_client):
    """分类只有 8 题，请求 20 题必须失败，不能生成缩水的一局。"""
    response = student_client.post(
        "/api/quiz/sessions", json={"questionCount": 20, "categoryId": "cat_a"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "QUESTION_NOT_ENOUGH"


def test_invalid_question_count(student_client):
    response = student_client.post("/api/quiz/sessions", json={"questionCount": 7})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 作答与判分
def test_answer_upsert_keeps_single_record(student_client):
    session = _start_session(student_client, 10)
    target = _pick(session, "SINGLE")
    url = f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}"

    assert student_client.put(url, json={"answer": "A"}).status_code == 200
    data = _payload(student_client.put(url, json={"answer": "C"}))
    assert data["userAnswer"] == "C"

    restored = _payload(student_client.get(f"/api/quiz/sessions/{session['sessionId']}"))
    first = next(q for q in restored["questions"] if q["sessionQuestionId"] == target["sessionQuestionId"])
    assert first["selected"] == "C"   # 只保留最后选择


def test_invalid_option_rejected(student_client):
    """单选题只接受 A–D；'E' 必须被拒绝。"""
    session = _start_session(student_client, 10)
    target = _pick(session, "SINGLE")
    url = f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}"
    response = student_client.put(url, json={"answer": "E"})
    assert response.status_code == 400


def test_judge_rejects_option_outside_ab(student_client):
    """判断题只有「正确 / 错误」两个键位，C 不在其中。"""
    session = _typed_session(student_client)
    target = _pick(session, "JUDGE")
    url = f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}"
    assert student_client.put(url, json={"answer": "A"}).status_code == 200
    assert student_client.put(url, json={"answer": "C"}).status_code == 400


def test_blank_answer_is_saved_as_free_text(student_client):
    """填空题保存的是原文，不是键位。"""
    session = _typed_session(student_client)
    target = _pick(session, "BLANK")
    url = f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}"
    data = _payload(student_client.put(url, json={"answer": "  我觉得是 kw0  "}))
    assert data["userAnswer"] == "我觉得是 kw0"          # 两端空白被去掉，中间保留

    restored = _payload(student_client.get(f"/api/quiz/sessions/{session['sessionId']}"))
    saved = next(q for q in restored["questions"] if q["sessionQuestionId"] == target["sessionQuestionId"])
    assert saved["selected"] == "我觉得是 kw0"


def test_blank_rejects_overlong_answer(student_client):
    """超过 60 字的输入直接拒绝，而不是存下来再判错。"""
    session = _typed_session(student_client)
    target = _pick(session, "BLANK")
    url = f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}"
    assert student_client.put(url, json={"answer": "长" * 61}).status_code >= 400
    assert student_client.put(url, json={"answer": "   "}).status_code >= 400


def test_blank_grading_is_lenient_but_not_lawless(student_client):
    """填空题判分口径：答出关键字、无污言秽语、不明显离谱 → 算对。

    这一条是用户明确要求的规则，所以逐种情形都断言到：
    纯关键字、关键字夹在句子里 → 对；答非所问、带脏话 → 错。
    """
    import re

    session = _typed_session(student_client)
    blanks = [q for q in session["questions"] if q["qtype"] == "BLANK"]
    judges = [q for q in session["questions"] if q["qtype"] == "JUDGE"]
    assert (len(blanks), len(judges)) == (10, 10)

    def keyword_of(question) -> str:
        return "kw" + re.search(r"kw(\d+)", question["content"]).group(1)

    base = f"/api/quiz/sessions/{session['sessionId']}/answers"
    # 四道题分别对应四种情形
    cases = [
        (blanks[0], keyword_of(blanks[0]), True),                    # 只写关键字
        (blanks[1], f"我 觉得 是 {keyword_of(blanks[1])} 吧", True),   # 关键字在句子里
        (blanks[2], "完全无关的回答", False),                          # 没命中关键字
        (blanks[3], f"你这个傻逼 {keyword_of(blanks[3])}", False),     # 带污言秽语
    ]
    for question, answer, _ in cases:
        response = student_client.put(f"{base}/{question['sessionQuestionId']}", json={"answer": answer})
        assert response.status_code == 200, response.text

    for question in blanks[4:]:
        student_client.put(f"{base}/{question['sessionQuestionId']}", json={"answer": "不知道"})
    for question in judges:                     # conftest 的判断题标准答案都是 A
        student_client.put(f"{base}/{question['sessionQuestionId']}", json={"answer": "A"})

    result = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit", json={}))
    assert result["questionCount"] == 20
    assert result["correctCount"] == 12          # 填空 2 对 + 判断 10 对
    assert result["score"] == 60
    assert result["unansweredCount"] == 0

    detail = {row["sessionQuestionId"]: row for row in result["questions"]}
    for question, _, expected in cases:
        row = detail[question["sessionQuestionId"]]
        assert row["qtype"] == "BLANK"
        assert row["isCorrect"] is expected, (question["content"], row)
        assert row["keywords"], "终态必须给出判分要点，好让用户知道差在哪"
    hit = detail[cases[0][0]["sessionQuestionId"]]
    assert hit["matchedKeywords"] == [keyword_of(cases[0][0])]


def test_result_page_renders_blank_review(student_client):
    """结果页要能解释填空题为什么算对/算错。"""
    session = _typed_session(student_client)
    blank = _pick(session, "BLANK")
    base = f"/api/quiz/sessions/{session['sessionId']}/answers"
    student_client.put(f"{base}/{blank['sessionQuestionId']}", json={"answer": "kw0"})
    student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit", json={})

    page = student_client.get(f"/result/{session['sessionId']}")
    assert page.status_code == 200
    assert "判分要点" in page.text
    assert "参考答案" in page.text


def test_answer_from_other_session_rejected(student_client):
    first = _start_session(student_client, 5)
    second = _start_session(student_client, 5)
    foreign = second["questions"][0]["sessionQuestionId"]
    response = student_client.put(
        f"/api/quiz/sessions/{first['sessionId']}/answers/{foreign}", json={"answer": "A"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NOT_IN_SESSION"


def test_score_is_computed_server_side_and_reproducible(student_client):
    """全选 A：score 必须等于服务端按快照算出的正确数 × 100 / 题数。"""
    session = _start_session(student_client, 10)
    for question in session["questions"]:
        student_client.put(
            f"/api/quiz/sessions/{session['sessionId']}/answers/{question['sessionQuestionId']}",
            json={"answer": "A"},
        )
    result = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))

    assert result["score"] == round(result["correctCount"] * 100 / 10)
    assert result["correctCount"] == sum(1 for q in result["questions"] if q["isCorrect"])
    assert result["unansweredCount"] == 0
    assert result["grade"] in {"EXCELLENT", "GOOD", "PASS", "NEEDS_WORK"}
    assert all("correctAnswer" in q for q in result["questions"])   # 终态后才给标准答案


def test_unanswered_counted_as_wrong(student_client):
    session = _start_session(student_client, 5)
    result = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))
    assert result["unansweredCount"] == 5
    assert result["score"] == 0
    assert result["grade"] == "NEEDS_WORK"


def test_submit_is_idempotent(student_client):
    """重复提交返回同一结果，且不重复计分。"""
    session = _start_session(student_client, 5)
    target = session["questions"][0]
    student_client.put(
        f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}",
        json={"answer": "A"},
    )
    first = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))
    second = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))
    assert first["score"] == second["score"]
    assert first["spEarned"] == second["spEarned"]


def test_answering_after_submit_is_rejected(student_client):
    session = _start_session(student_client, 5)
    student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit")
    target = session["questions"][0]
    response = student_client.put(
        f"/api/quiz/sessions/{session['sessionId']}/answers/{target['sessionQuestionId']}",
        json={"answer": "B"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_FINISHED"


# ------------------------------------------------------------------ 权限
def test_cannot_access_other_users_session(student_client, other_client):
    session = _start_session(student_client, 5)
    response = other_client.get(f"/api/quiz/sessions/{session['sessionId']}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_student_cannot_use_admin_api(student_client):
    response = student_client.get("/api/admin/questions")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_admin_can_list_questions(admin_client):
    data = _payload(admin_client.get("/api/admin/questions"))
    assert data["total"] >= 16
    assert "correctAnswer" in data["items"][0]


def test_admin_can_filter_questions_by_type(admin_client):
    data = _payload(admin_client.get("/api/admin/questions?qtype=BLANK&pageSize=50"))
    assert data["total"] >= 10
    assert all(item["qtype"] == "BLANK" for item in data["items"])
    assert all(item["keywords"] for item in data["items"])   # 填空题必须带判分要点

    judges = _payload(admin_client.get("/api/admin/questions?qtype=JUDGE&pageSize=50"))
    assert judges["total"] >= 10
    assert all(item["qtype"] == "JUDGE" for item in judges["items"])

    bad = admin_client.get("/api/admin/questions?qtype=ESSAY")
    assert bad.status_code == 400


def test_admin_can_create_blank_question(admin_client):
    payload = {
        "content": "南开大学创办于______年。",
        "qtype": "BLANK",
        "correctAnswer": "1919 年",
        "keywords": ["1919"],
        "explanation": "严修、张伯苓 1919 年创办南开大学。",
        "difficulty": "EASY",
        "categoryId": "cat_a",
    }
    created = _payload(admin_client.post("/api/admin/questions", json=payload))
    assert created["qtype"] == "BLANK"
    assert created["qtypeLabel"] == "填空题"
    assert created["options"] == {}                 # 填空题没有选项
    assert created["keywords"] == ["1919"]


def test_admin_can_create_judge_question(admin_client):
    payload = {
        "content": "南开大学创办于 1919 年。",
        "qtype": "JUDGE",
        "correctAnswer": "A",
        "explanation": "严修、张伯苓 1919 年创办南开大学。",
        "difficulty": "EASY",
        "categoryId": "cat_a",
    }
    created = _payload(admin_client.post("/api/admin/questions", json=payload))
    assert created["qtype"] == "JUDGE"
    assert created["options"] == {"A": "正确", "B": "错误"}


def test_admin_blank_without_keywords_is_rejected(admin_client):
    """填空题没有关键字就判不动分，必须在入口拦住。"""
    response = admin_client.post("/api/admin/questions", json={
        "content": "填空题没有配关键字",
        "qtype": "BLANK",
        "correctAnswer": "参考答案",
        "keywords": [],
        "explanation": "解析",
        "difficulty": "EASY",
        "categoryId": "cat_a",
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_single_question_still_requires_four_options(admin_client):
    """单选不能因为放宽了三种题型，就把四个选项的必填一起放宽掉。"""
    response = admin_client.post("/api/admin/questions", json={
        "content": "缺选项的单选",
        "qtype": "SINGLE",
        "options": {"A": "甲", "B": "乙"},
        "correctAnswer": "A",
        "explanation": "解析",
        "difficulty": "EASY",
        "categoryId": "cat_a",
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 题库 CRUD
def test_admin_create_and_edit_question(admin_client):
    before = _payload(admin_client.get("/api/admin/questions/stats"))["active"]
    payload = {
        "content": "测试新增题目：南开大学创办于哪一年？",
        "options": {"A": "1919 年", "B": "1904 年", "C": "1937 年", "D": "1946 年"},
        "correctAnswer": "A",
        "explanation": "1919 年由严修、张伯苓创办。",
        "difficulty": "EASY",
        "categoryId": "cat_a",
    }
    created = _payload(admin_client.post("/api/admin/questions", json=payload))
    assert created["content"] == payload["content"]

    after = _payload(admin_client.get("/api/admin/questions/stats"))["active"]
    assert after == before + 1

    updated = _payload(
        admin_client.put(
            f"/api/admin/questions/{created['id']}",
            json={**payload, "difficulty": "MEDIUM"},
        )
    )
    assert updated["difficulty"] == "MEDIUM"

    disabled = _payload(
        admin_client.patch(f"/api/admin/questions/{created['id']}/active", json={"isActive": False})
    )
    assert disabled["isActive"] is False


def test_editing_question_does_not_change_history(admin_client, student_client):
    """核心不可变规则 2：会话保存快照，题库编辑不影响旧成绩。"""
    session = _start_session(student_client, 5)
    for question in session["questions"]:
        student_client.put(
            f"/api/quiz/sessions/{session['sessionId']}/answers/{question['sessionQuestionId']}",
            json={"answer": "A"},
        )
    before = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))

    question_id = _payload(
        admin_client.get("/api/admin/questions?keyword=创校类题目&pageSize=1")
    )["items"][0]["id"]
    detail = _payload(admin_client.get(f"/api/admin/questions/{question_id}"))
    admin_client.put(
        f"/api/admin/questions/{question_id}",
        json={
            "content": detail["content"] + "（题干已被改写）",
            "options": detail["options"],
            "correctAnswer": "D",         # 把标准答案从 A 改成 D
            "explanation": detail["explanation"],
            "difficulty": detail["difficulty"],
            "categoryId": detail["categoryId"],
        },
    )

    after = _payload(student_client.get(f"/api/quiz/sessions/{session['sessionId']}/result"))
    assert after["score"] == before["score"]
    assert after["correctCount"] == before["correctCount"]


# ------------------------------------------------------------------ 统计与排行榜
def test_dashboard_and_history_and_leaderboard(student_client):
    session = _start_session(student_client, 5)
    for question in session["questions"]:
        student_client.put(
            f"/api/quiz/sessions/{session['sessionId']}/answers/{question['sessionQuestionId']}",
            json={"answer": "A"},
        )
    student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit")

    dashboard = _payload(student_client.get("/api/dashboard"))
    assert dashboard["stats"]["finishedSessions"] >= 1
    assert dashboard["stats"]["rank"] is not None

    history = _payload(student_client.get("/api/history"))
    assert history["total"] >= 1
    assert history["items"][0]["score"] is not None

    board = _payload(student_client.get("/api/leaderboard"))
    assert board["myRank"]["username"] == "test"
    assert board["items"][0]["bestScore"] >= board["items"][-1]["bestScore"]


def test_in_progress_session_excluded_from_stats(student_client):
    before = _payload(student_client.get("/api/dashboard"))["stats"]["finishedSessions"]
    _start_session(student_client, 5)   # 创建后不提交
    after = _payload(student_client.get("/api/dashboard"))["stats"]["finishedSessions"]
    assert after == before


# ------------------------------------------------------------------ 超时
def test_timeout_settlement(student_client):
    """把截止时间改到过去，下一次访问必须结算为 TIMEOUT。"""
    from app.core.db import SessionLocal
    from app.models import QuizSession

    session = _start_session(student_client, 5)
    session_id = session["sessionId"]

    with SessionLocal() as db:
        row = db.get(QuizSession, session_id)
        row.expires_at = utcnow() - timedelta(seconds=30)
        db.commit()

    result = _payload(student_client.post(f"/api/quiz/sessions/{session_id}/submit"))
    assert result["status"] == "TIMEOUT"
    assert result["score"] == 0
    assert result["unansweredCount"] == 5


def test_lazy_timeout_on_read(student_client):
    """即使不主动提交，读取会话也会触发超时结算。"""
    from app.core.db import SessionLocal
    from app.models import QuizSession

    session = _start_session(student_client, 5)
    with SessionLocal() as db:
        row = db.get(QuizSession, session["sessionId"])
        row.expires_at = utcnow() - timedelta(seconds=10)
        db.commit()

    view = _payload(student_client.get(f"/api/quiz/sessions/{session['sessionId']}"))
    assert view["status"] == "TIMEOUT"


# ------------------------------------------------------------------ 奖励
def test_rewards_summary_and_achievements(student_client):
    session = _start_session(student_client, 5)
    for question in session["questions"]:
        student_client.put(
            f"/api/quiz/sessions/{session['sessionId']}/answers/{question['sessionQuestionId']}",
            json={"answer": "A"},
        )
    result = _payload(student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))
    assert result["reward"]["spEarned"] >= 20       # 完成基数

    summary = _payload(student_client.get("/api/rewards/summary"))
    assert summary["studyPoints"] >= 20
    assert summary["level"]["code"].startswith("L")

    wall = _payload(student_client.get("/api/rewards/achievements"))
    codes = {item["code"] for item in wall["items"] if item["unlocked"]}
    assert "FIRST_QUIZ" in codes


def test_reward_client_cannot_report_points(student_client):
    """奖励只读：不存在的写入接口必须 404/405，客户端无法上报 SP。"""
    response = student_client.post("/api/rewards/summary", json={"studyPoints": 99999})
    assert response.status_code in (404, 405)


def test_recompute_matches_online_value(admin_client, student_client):
    """重算结果必须与在线值一致（可审计）。"""
    session = _start_session(student_client, 5)
    student_client.put(
        f"/api/quiz/sessions/{session['sessionId']}/answers/{session['questions'][0]['sessionQuestionId']}",
        json={"answer": "A"},
    )
    student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit")

    online = _payload(student_client.get("/api/rewards/summary"))["studyPoints"]
    me = _payload(student_client.get("/api/auth/me"))
    recomputed = _payload(
        admin_client.post("/api/admin/rewards/recompute", json={"userId": me["id"]})
    )["results"][0]
    assert recomputed["studyPoints"] == online


# ------------------------------------------------------------------ RAG 降级
def test_rag_returns_graceful_fallback(student_client):
    """测试环境 RAG_ENABLED=false，接口必须明确降级而不是抛错。"""
    session = _start_session(student_client, 5)
    student_client.post(f"/api/quiz/sessions/{session['sessionId']}/submit")
    response = student_client.post(
        "/api/rag/explanations",
        json={
            "sessionId": session["sessionId"],
            "sessionQuestionId": session["questions"][0]["sessionQuestionId"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RAG_DISABLED"


def test_rag_rejects_unfinished_session(student_client):
    """未结束的会话不能生成增强解析（防止提前泄露解析）。"""
    session = _start_session(student_client, 5)
    response = student_client.post(
        "/api/rag/explanations",
        json={
            "sessionId": session["sessionId"],
            "sessionQuestionId": session["questions"][0]["sessionQuestionId"],
        },
    )
    assert response.status_code in (409, 404)
