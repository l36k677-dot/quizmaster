"""新手指引：步骤定义、渲染契约与账号级持久化。

分工与这条测试线的边界：
  · 这里只测「服务端说了什么」—— 步骤数据是否自洽、锚点是否真的渲染出来、
    已读标记是否跟着账号走；
  · 「高亮框对不对齐、Esc 能不能关」属于浏览器行为，由
    .workbuddy/tmp/verify-tour.mjs 的 CDP 探针负责（本文件测不了）。
"""

from __future__ import annotations

import json
import re

import pytest

from app.core.config import TEMPLATE_DIR
from app.services import tour as tour_service

CREDIT_SOURCE = "紫菡"          # 形象原型的出处关键词，界面上必须能看到


def _tour_payload(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="tour-data">(.*?)</script>', html, re.S)
    assert match, "页面里没有 #tour-data：指引数据段丢失"
    return json.loads(match.group(1))


def _register(client, username: str):
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Tour@2026x", "displayName": "指引测试"},
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ 步骤定义
def test_steps_are_self_consistent():
    keys = tour_service.step_keys()
    assert len(keys) == len(set(keys)), f"步骤 key 重复：{keys}"
    assert len(keys) >= 6, "指引太短，起不到导览作用"

    for step in tour_service.TOUR_STEPS:
        assert step["title"].strip(), f"{step['key']} 缺标题"
        assert step["body"].strip(), f"{step['key']} 缺正文"
        assert step["pose"] in tour_service.MASCOT_POSES, f"{step['key']} 姿势非法"
        assert step["placement"] in tour_service.TOUR_PLACEMENTS, f"{step['key']} 位置非法"
        if step["target"]:
            assert step["target"] in tour_service.TOUR_ANCHORS, (
                f"{step['key']} 用了未登记的锚点 {step['target']} —— "
                "请一并补进 TOUR_ANCHORS，否则没人知道这个锚点该挂在哪"
            )


def test_steps_start_and_end_without_target():
    """首尾两步是不带准星的欢迎 / 收尾，否则开场就会去高亮一个还不该动的东西。"""
    assert tour_service.TOUR_STEPS[0]["target"] == ""
    assert tour_service.TOUR_STEPS[-1]["target"] == ""


def test_credit_names_the_original_ip():
    assert CREDIT_SOURCE in tour_service.MASCOT_CREDIT


# ------------------------------------------------------------------ 渲染契约
def test_dashboard_renders_layer_and_hidden_trigger(student_client):
    html = student_client.get("/dashboard").text
    assert 'id="tour-layer"' in html
    # 无脚本时不能留下点了没反应的控件
    assert re.search(r'data-action="tour-start"\s+hidden', html), "重开入口默认必须 hidden"
    assert 'aria-modal="true"' in html, "指引是一段模态对话，需要无障碍语义"


def test_dashboard_payload_matches_definition(student_client):
    payload = _tour_payload(student_client.get("/dashboard").text)
    assert payload["version"] == tour_service.TOUR_VERSION
    assert len(payload["steps"]) == len(tour_service.TOUR_STEPS)
    assert [s["key"] for s in payload["steps"]] == tour_service.step_keys()
    # 文案随数据一起下发，前端不另存一份
    assert payload["steps"][0]["title"] == tour_service.TOUR_STEPS[0]["title"]


def test_mascot_renders_four_transparent_poses(student_client):
    """荷宝必须背景透明：SVG 里不出现铺满画布的 <rect>，也不引外部图片。

    一次渲染出的荷宝不止指引卡里那 4 个（首页空状态也有一只），
    所以分两步断言：卡里恰好 4 个姿势；页面上**每一只**都透明。
    """
    html = student_client.get("/dashboard").text
    card = re.search(r'<div class="tour-mascot".*?</div>\s*<div class="tour-panel"', html, re.S)
    assert card, "指引卡里没有找到荷宝容器"
    # 只数 SVG 自己的 data-pose：容器上那个属性是「当前显示哪一张」，
    # 由脚本改写，同一份 HTML 里必然也出现一次，不能混进来一起数
    poses = re.findall(r'<svg class="mascot-svg" data-pose="(\w+)"', card.group(0))
    assert sorted(poses) == sorted(tour_service.MASCOT_POSES), f"指引卡里的姿势不齐：{poses}"

    svgs = re.findall(r"<svg class=\"mascot-svg\".*?</svg>", html, re.S)
    assert len(svgs) >= 4
    for svg in svgs:
        assert "<rect" not in svg, "出现了矩形底色 —— 荷宝必须背景透明"
        assert "viewBox" in svg
        assert 'aria-hidden="true"' in svg, "纯装饰图形不该进入无障碍朗读"
        assert "<image" not in svg, "不得内嵌位图，避免引出额外的图片文件与版权素材"


def test_tour_is_page_aware(student_client):
    """同一份定义，在首页要跳过「只有知识库页才有」的那一步。"""
    html = student_client.get("/dashboard").text
    rendered = set(re.findall(r'data-tour="([\w-]+)"', html))
    expected = [
        step["key"] for step in tour_service.TOUR_STEPS
        if not step["target"]
        or re.search(r'data-tour="([\w-]+)"', step["target"]).group(1) in rendered
    ]
    assert "kb-cards" not in expected, "首页没有卡片工具条，这一步该被跳过"
    assert "dashboard-start" in expected and "nav-knowledge" in expected
    assert len(expected) == len(tour_service.TOUR_STEPS) - 1


def test_knowledge_page_has_its_own_anchor(student_client):
    html = student_client.get("/knowledge").text
    assert 'data-tour="kb-tools"' in html


def test_login_page_shows_mascot_but_no_tour(student_client):
    """登录页只让荷宝露面，不放覆盖层：那时还没有账号，也就没有「已看过」。"""
    anonymous = student_client
    anonymous.post("/api/auth/logout", json={})
    html = anonymous.get("/login").text
    assert html.count("mascot-svg") == 1
    assert 'id="tour-layer"' not in html
    assert "荷宝会带你逛一圈" in html


# ------------------------------------------------------------------ 账号级持久化
def test_seen_state_requires_login(client):
    assert client.get("/api/tour/state").status_code == 401
    assert client.post("/api/tour/seen", json={}).status_code == 401


def test_auto_start_only_before_first_show(make_client):
    """首次进首页自动开场，记过之后不再自动开场 —— 但入口仍可手动重开。"""
    fresh = make_client()
    _register(fresh, "tour_fresh")

    first = _tour_payload(fresh.get("/dashboard").text)
    assert first["seen"] is False
    assert first["autoStart"] is True

    seen = fresh.post("/api/tour/seen", json={})
    assert seen.status_code == 200
    assert seen.json()["data"]["seen"] is True

    second = _tour_payload(fresh.get("/dashboard").text)
    assert second["seen"] is True
    assert second["autoStart"] is False, "看过之后不该再自动开场"
    assert len(second["steps"]) == len(tour_service.TOUR_STEPS), "手动重开时步骤依然齐备"


def test_seen_endpoint_is_idempotent(make_client):
    client = make_client()
    _register(client, "tour_idempotent")
    for _ in range(3):
        response = client.post("/api/tour/seen", json={})
        assert response.status_code == 200
        assert response.json()["data"]["seen"] is True
    assert client.get("/api/tour/state").json()["data"]["seen"] is True


def test_seen_is_per_account(make_client):
    """已读跟着账号走：另一个账号第一次进来，照样自动开场。"""
    first = make_client()
    _register(first, "tour_user_a")
    first.post("/api/tour/seen", json={})

    second = make_client()
    _register(second, "tour_user_b")
    payload = _tour_payload(second.get("/dashboard").text)
    assert payload["autoStart"] is True, "换账号不该继承别人的已读标记"


def test_other_pages_never_auto_start(student_client):
    """自动开场只发生在首页：知识库/排行榜这类页面不该被指引盖住。"""
    for path in ("/knowledge", "/leaderboard", "/history"):
        payload = _tour_payload(student_client.get(path).text)
        assert payload["autoStart"] is False, f"{path} 不该自动开场"
        assert payload["steps"], f"{path} 应仍可手动重开指引"


@pytest.mark.parametrize("path", ["/dashboard", "/knowledge"])
def test_credit_is_visible_on_page(student_client, path):
    """署名必须在页面上，而不只存在于代码注释里。"""
    assert CREDIT_SOURCE in student_client.get(path).text


def test_template_directory_is_where_we_think():
    """防止测试与模板目录漂移（改了目录结构时这里要一起改）。"""
    assert (TEMPLATE_DIR / "_mascot.html").exists()
    assert (TEMPLATE_DIR / "_tour.html").exists()
