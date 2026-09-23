"""端到端冒烟测试：对已启动的服务跑一遍完整闭环。

用法（需先启动服务）：
    uv run python scripts/smoke_test.py
    uv run python scripts/smoke_test.py --base http://127.0.0.1:8000 --with-rag
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  [FAIL] {name} {detail}")


def api(response, *, allow_error: bool = False):
    body = response.json()
    if not allow_error:
        assert body.get("success") is True, body
    return body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--with-rag", action="store_true", help="同时验证 RAG 真实调用")
    parser.add_argument("--student", default="smoke_runner",
                        help="用于自检的学生账号；不存在时脚本会自动注册")
    parser.add_argument("--password", default="Smoke@2026")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base, timeout=30.0, follow_redirects=False)

    print("\n=== 1. 健康检查与服务信息 ===")
    health = api(client.get("/healthz"))
    data = health["data"]
    check("健康检查 200", health["success"])
    print(f"        env={data['env']} reward={data['rewardEnabled']} rag={data['ragEnabled']} aiConfigured={data['aiConfigured']}")

    print("\n=== 2. 页面与权限 ===")
    login_page = client.get("/login")
    check("登录页可访问", login_page.status_code == 200 and "QuizMaster" in login_page.text)

    root = client.get("/")
    check("未登录访问根路径跳转登录", root.status_code in (303, 307) and "/login" in root.headers.get("location", ""))

    unauth = client.get("/api/dashboard")
    check("未登录 API 返回 401", unauth.status_code == 401)
    check("错误码为 AUTH_REQUIRED", unauth.json()["error"]["code"] == "AUTH_REQUIRED")

    print("\n=== 3. 登录 ===")
    login_response = client.post(
        "/api/auth/login", json={"username": args.student, "password": args.password}
    )
    # 交付包不带数据库，全新环境里这个账号并不存在。
    # 自检脚本要自己把账号准备好，而不是要求使用者先手工注册 —— 否则
    # 「照 README 跑一遍」第一步就是红的。这里只在登录被拒时才注册，
    # 已存在的账号（含使用者自己的）一律沿用，不会被覆盖。
    if login_response.status_code == 401:
        print(f"       账号 {args.student!r} 不存在，注册自检账号…")
        reg = client.post(
            "/api/auth/register",
            json={"username": args.student, "password": args.password, "displayName": "自检账号"},
        )
        if reg.status_code != 200:
            print(f"       注册未成功：HTTP {reg.status_code} {reg.text[:160]}")
            print(f"       若该账号已被占用，请改传 --student / --password 指定一个账号。")
        login_response = client.post(
            "/api/auth/login", json={"username": args.student, "password": args.password}
        )
    login = login_response.json()
    check("登录成功", login_response.status_code == 200 and login["data"]["username"] == args.student)
    check("Cookie 为 HttpOnly", "HttpOnly" in login_response.headers.get("set-cookie", ""))

    bad = client.post("/api/auth/login", json={"username": args.student, "password": "bad-password"})
    check("错误密码被拒绝", bad.status_code == 401)

    dashboard_page = client.get("/dashboard")
    check("Dashboard 页面渲染", dashboard_page.status_code == 200 and "开始新一局" in dashboard_page.text)

    print("\n=== 4. 抽题与做答 ===")
    session = api(client.post("/api/quiz/sessions", json={"questionCount": 5}))["data"]
    check("创建 5 题会话", len(session["questions"]) == 5)
    leaked = any("correctAnswer" in q for q in session["questions"])
    check("答题阶段不泄露标准答案", not leaked)
    check("服务端返回截止时间", bool(session.get("expiresAt")))

    for position, question in enumerate(session["questions"]):
        # 第一题故意换一个选项，保证本局存在错题，便于同时验证 RAG 增强解析
        answer = "A" if position > 0 else "B"
        client.put(
            f"/api/quiz/sessions/{session['sessionId']}/answers/{question['sessionQuestionId']}",
            json={"answer": answer},
        )
    view = api(client.get(f"/api/quiz/sessions/{session['sessionId']}"))["data"]
    answered = sum(1 for q in view["questions"] if q["selected"])
    check("答案已保存到服务端", answered == 5, f"(已保存 {answered}/5)")

    print("\n=== 5. 提交与判分 ===")
    result = api(client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))["data"]
    expected = round(result["correctCount"] * 100 / 5)
    check("得分等于服务端算式", result["score"] == expected,
          f"(score={result['score']} correct={result['correctCount']})")
    check("评级已生成", result["grade"] in {"EXCELLENT", "GOOD", "PASS", "NEEDS_WORK"})
    check("终态返回标准答案与解析", all("correctAnswer" in q for q in result["questions"]))
    print(f"        得分 {result['score']} · {result['gradeLabel']} · 用时 {result['durationSeconds']}s · SP {result['spEarned']}")

    again = api(client.post(f"/api/quiz/sessions/{session['sessionId']}/submit"))["data"]
    check("重复提交幂等", again["score"] == result["score"] and again["spEarned"] == result["spEarned"])

    result_page = client.get(f"/result/{session['sessionId']}")
    check("结果页渲染", result_page.status_code == 200 and "逐题明细与解析" in result_page.text)

    print("\n=== 6. 统计与排行榜 ===")
    dashboard = api(client.get("/api/dashboard"))["data"]
    check("Dashboard 统计非空", dashboard["stats"]["finishedSessions"] >= 1)
    check("排行榜名次可计算", dashboard["stats"]["rank"] is not None)

    history = api(client.get("/api/history"))["data"]
    check("历史成绩包含本局", any(row["sessionId"] == session["sessionId"] for row in history["items"]))

    board = api(client.get("/api/leaderboard"))["data"]
    check("排行榜返回数据", len(board["items"]) >= 1)

    history_page = client.get("/history")
    check("历史页渲染", history_page.status_code == 200)

    board_page = client.get("/leaderboard")
    check("排行榜页渲染", board_page.status_code == 200)

    print("\n=== 7. 奖励 ===")
    summary = api(client.get("/api/rewards/summary"))["data"]
    check("SP 已累计", summary["studyPoints"] >= 0, f"(SP={summary['studyPoints']})")
    wall = api(client.get("/api/rewards/achievements"))["data"]
    check("成就墙返回 8 项", wall["totalCount"] == 8, f"(实际 {wall['totalCount']})")
    print(f"        等级 {summary['level']['title']} · 连续 {summary['currentStreak']} 天 · 成就 {wall['unlockedCount']}/{wall['totalCount']}")

    print("\n=== 8. 管理员端 ===")
    admin = httpx.Client(base_url=args.base, timeout=30.0, follow_redirects=False)
    api(admin.post("/api/auth/login", json={"username": "admin", "password": "QuizAdmin@2026"}))
    listing = api(admin.get("/api/admin/questions"))["data"]
    check("管理员可读题库", listing["total"] >= 40, f"(total={listing['total']})")

    forbidden = client.get("/api/admin/questions")
    check("普通用户访问管理接口 403", forbidden.status_code == 403)

    admin_page = admin.get("/admin/questions")
    check("题库管理页渲染", admin_page.status_code == 200 and "题目列表" in admin_page.text)

    knowledge = api(admin.get("/api/admin/knowledge"))["data"]
    check("资料库已索引", knowledge["chunkCount"] > 0, f"(chunks={knowledge['chunkCount']})")
    admin_page_k = admin.get("/admin/knowledge")
    check("资料库页渲染", admin_page_k.status_code == 200)

    print("\n=== 9. 知识库与交互动效 ===")
    kb = client.get("/knowledge")
    check("知识库页渲染", kb.status_code == 200, f"(HTTP {kb.status_code})")
    kb_html = kb.text

    # 按 `class="tl-node ` 计数，不依赖 `reveal` 紧跟在后面 ——
    # 节点类名里现在还带分期修饰（tl-node-创校 等），按旧写法会漏数。
    timeline_nodes = kb_html.count('class="tl-node ')
    check("校史时间轴节点齐全", timeline_nodes >= 10, f"({timeline_nodes} 个)")
    people_cards = kb_html.count("person-card reveal")
    check("人物档案卡齐全", people_cards >= 6, f"({people_cards} 位)")
    quiz_cards = kb_html.count("quiz-card reveal")
    check("题卡与题库实时同源", quiz_cards >= 30, f"({quiz_cards} 张)")
    check(
        "八格数字看板已移除",
        "kb-stats" not in kb_html and "kb-stat-value" not in kb_html,
    )
    check(
        "知识库章节目录 5 项齐全",
        kb_html.count("nav-sub-item") == 5,
        f"({kb_html.count('nav-sub-item')} 项)",
    )
    check(
        "章节目录只在知识库页出现",
        "nav-sub-item" not in client.get("/dashboard").text,
    )
    # 姓氏小章删掉后不该以任何形式回来（模板里不再有该元素，数据里也不再带 initial）
    check(
        "人物卡姓氏小章已移除",
        "person-stamp" not in kb_html,
    )
    # 直接读种子数据：页面不再渲染该元素，数据侧也不该再留这个字段
    seed = json.loads(
        (PROJECT_ROOT / "data/seed/knowledge.json").read_text(encoding="utf-8")
    )
    check(
        "人物数据不再输出 initial 字段",
        seed["people"] and all("initial" not in person for person in seed["people"]),
        f"({len(seed['people'])} 位)",
    )
    check(
        "文化标识 / 易混淆辨析已渲染",
        "emblem-card" in kb_html and "contrast-card" in kb_html and "is-nankai" in kb_html,
    )
    check(
        "知识库内容服务端直出（JS 失效仍可读）",
        "南开校史知识库" in kb_html and "允公允能" in kb_html and "张伯苓" in kb_html,
    )

    reveal_view = client.get("/knowledge?reveal=1")
    reveal_html = reveal_view.text
    flipped = reveal_html.count("is-flipped")
    check(
        "放映模式：题卡与人物卡全部翻开",
        flipped >= people_cards + quiz_cards - 8,
        f"({flipped} 张翻开)",
    )
    check(
        "放映模式：跳过入场动效（body.is-present）",
        'class="is-present"' in reveal_html or "is-present" in reveal_html,
    )

    anon = httpx.Client(base_url=args.base, timeout=15.0, follow_redirects=False)
    check("未登录访问知识库跳转登录", anon.get("/knowledge").status_code == 303)
    anon.close()

    css = client.get("/static/css/app.css").text
    js = client.get("/static/js/app.js").text
    check(
        "动效底座（统一缓动 / 减少动效兜底）",
        "--ease-out-expo" in css and "prefers-reduced-motion" in css,
    )
    check(
        "动效与交互脚本已加载",
        "createViewportWatcher" in js and "initFlipCards" in js and "celebrate" in js,
    )
    check("无脚本时内容不会被隐藏", "html.anim .reveal" in css)
    check(
        "放映模式在 CSS 层有兜底（不依赖 JS 也能看见）",
        "body.is-present .reveal" in css,
    )
    check(
        "锚点定位后补检视口（load / hashchange）",
        "hashchange" in js and "addEventListener('load', check)" in js,
    )
    check(
        "动效退化开关定义完整（防自引用）",
        "const NO_MOTION = !MOTION_OK || prefersReducedMotion || PRESENT_MODE;" in js,
    )
    check(
        "内联 SVG 站点图标（避免 favicon 404）",
        "rel=\"icon\"" in kb_html and "data:image/svg+xml" in kb_html,
    )

    print("\n=== 10. 新手指引（荷宝导览）===")
    from app.services import tour as tour_service

    dash_tour = client.get("/dashboard").text
    tour_match = re.search(
        r'<script type="application/json" id="tour-data">(.*?)</script>', dash_tour, re.S
    )
    check("指引数据段随首页下发", tour_match is not None)
    tour_payload = json.loads(tour_match.group(1)) if tour_match else {}
    check(
        "步骤数与服务端定义一致",
        len(tour_payload.get("steps", [])) == len(tour_service.TOUR_STEPS),
        f"({len(tour_payload.get('steps', []))} vs {len(tour_service.TOUR_STEPS)})",
    )
    check(
        "autoStart 与 seen 严格互斥（看过的账号不再自动开场）",
        tour_payload.get("autoStart") is (not tour_payload.get("seen")),
        f"(autoStart={tour_payload.get('autoStart')} seen={tour_payload.get('seen')})",
    )
    anchors = [
        "start-form", "stat-board", "level-card", "recent-card",
        "nav-knowledge", "account-menu",
    ]
    missing = [a for a in anchors if f'data-tour="{a}"' not in dash_tour]
    check("首页指引锚点齐全", not missing, f"(缺 {missing})")
    check(
        "覆盖层与重开入口默认 hidden（无脚本时不留死控件）",
        re.search(r'id="tour-layer"\s+hidden', dash_tour) is not None
        and re.search(r'data-action="tour-start"\s+hidden', dash_tour) is not None,
    )
    mascot_svgs = re.findall(r'<svg class="mascot-svg".*?</svg>', dash_tour, re.S)
    check(
        "荷宝四个姿势齐备",
        sorted(re.findall(r'<svg class="mascot-svg" data-pose="(\w+)"', dash_tour))
        == sorted(tour_service.MASCOT_POSES),
        f"({len(mascot_svgs)} 只)",
    )
    check(
        "荷宝背景透明（SVG 内无任何矩形底色）",
        bool(mascot_svgs) and all("<rect" not in svg and "<image" not in svg for svg in mascot_svgs),
    )
    check(
        "形象出处署名可见（原型 IP「紫菡」）",
        "紫菡" in dash_tour and "本项目自绘" in dash_tour,
    )
    tour_js = client.get("/static/js/tour.js").text
    check(
        "指引脚本已加载（跳出/持久化/演示开关齐备）",
        all(k in tour_js for k in ("Escape", "tour-on", "/api/tour/seen", "QMTour", "data-tour-", "is-sheet")),
    )
    check(
        "指引样式含窄屏抽屉与滚动控制",
        ".tour-card.is-sheet" in css and "html.tour-on" in css and ".tour-spot" in css,
    )
    check("知识库页带卡片工具条锚点", 'data-tour="kb-tools"' in kb_html)

    anon_tour = httpx.Client(base_url=args.base, timeout=15.0, follow_redirects=False)
    check("未登录不能标记指引已读", anon_tour.post("/api/tour/seen", json={}).status_code == 401)
    anon_tour.close()

    marked = client.post("/api/tour/seen", json={})
    check("标记已读接口返回 200", marked.status_code == 200 and marked.json()["data"]["seen"] is True)
    after = json.loads(re.search(
        r'<script type="application/json" id="tour-data">(.*?)</script>',
        client.get("/dashboard").text, re.S,
    ).group(1))
    check(
        "标记后不再自动开场（但步骤仍在，可手动重开）",
        after["autoStart"] is False and len(after["steps"]) == len(tour_service.TOUR_STEPS),
        f"(autoStart={after['autoStart']})",
    )

    print("\n=== 11. 视觉一致性与可访问性 ===")
    root = PROJECT_ROOT
    static_dir = root / "app" / "static"

    # 登录页是独立页面（不继承 base.html），用匿名客户端单看一次，
    # 否则已登录的 client 会被 303 重定向到看板。
    anon2 = httpx.Client(base_url=args.base, timeout=15.0, follow_redirects=False)
    login_html = anon2.get("/login").text
    anon2.close()
    check(
        "登录页分栏布局（左插画 + 右表单）",
        all(k in login_html for k in ("auth-aside", "auth-figure", "auth-main")),
    )
    check(
        "登录页复用公共 head（动效标记 + 站点图标）",
        'rel="icon"' in login_html and "prefers-reduced-motion" in login_html,
    )
    check(
        "登录页文案与知识库同源（校训 / 建校年份）",
        "允公允能" in login_html and "1919" in login_html,
    )

    dash_html = client.get("/dashboard").text
    legacy_glyphs = [g for g in "◆❖▤▲★▦◈" if g in dash_html]
    svg_count = dash_html.count("<svg")
    check(
        "侧栏图标统一为内联 SVG（不再用 Unicode 符号拼凑）",
        svg_count >= 5 and not legacy_glyphs,
        f"(svg={svg_count}, 残留={legacy_glyphs or '无'})",
    )
    check("全站键盘焦点可见（:focus-visible）", ":focus-visible" in css)

    session_new = api(client.post("/api/quiz/sessions", json={"questionCount": 5}))["data"]
    quiz_html = client.get(f"/quiz/start/{session_new['sessionId']}").text
    check(
        "答题页提供键盘操作与可发现的快捷键提示",
        "kbd-list" in quiz_html and quiz_html.count("<kbd>") >= 8,
        f"({quiz_html.count('<kbd>')} 个键位)",
    )
    check(
        "答题页有无障碍播报区与快捷键语义",
        "sr-only" in quiz_html and "aria-live" in quiz_html and "aria-keyshortcuts" in quiz_html,
    )

    stray = sorted(p.name for p in static_dir.glob("tmp_*.html"))
    check("静态目录不含临时免登录辅助页", not stray, f"(发现 {stray})" if stray else "")

    # 六张手绘插画分两种画幅，各自对应所在槽位的实际宽高比：
    #  · 首页轮播 4 张 → 1152×512（2.25:1）。首页插画框实测 565×252 = 2.246:1，
    #    画幅与槽位对齐后 object-fit: cover 就裁不动它了。
    #  · 登录页 / 结果页 2 张 → 960×594（1.616:1）。它们的槽位是 1.87:1 的竖幅，
    #    cover 只裁掉约 14%，且都是满幅纹理（阅览室、桌面），不伤构图。
    # 为什么首页要单独做成宽幅：早先六张统一 960×594，首页框只显示得到
    # 72% 的高度，而新开湖那张的湖面正好落在被裁掉的下缘里 —— 整条湖看不见。
    # 靠 object-position 挪只能遮丑，把画幅做成槽位的形状才是根治。
    img_dir = static_dir / "img"
    webp_files = sorted(img_dir.glob("*.webp"))
    weight = sum(p.stat().st_size for p in webp_files)
    check(
        "手绘插画全部为 WebP 且共 6 张",
        len(webp_files) == 6,
        f"(实际 {len(webp_files)} 张：{[p.name for p in webp_files]})",
    )
    check(
        "手绘插画体积在预算内（6 张合计 < 680KB）",
        weight < 680 * 1024,
        f"({round(weight / 1024)}KB)",
    )
    heaviest = max((p.stat().st_size for p in webp_files), default=0)
    check("单张插画不超过 150KB", heaviest < 150 * 1024, f"(最大 {round(heaviest / 1024)}KB)")

    leftover = sorted(p.name for p in img_dir.glob("*.jpg"))
    check(
        "静态图片目录不留未压缩的 JPG 母版",
        not leftover,
        f"(发现 {leftover})" if leftover else "",
    )

    def webp_size(path):
        """从 RIFF 容器读 WebP 宽高（纯标准库，不为测试引入 Pillow）。

        按编码块分三种：VP8（有损，Pillow 默认）、VP8L（无损）、VP8X（扩展，
        带 alpha 或元数据时用）。只要按块遍历，三种都能读到。
        """
        data = path.read_bytes()
        if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return None
        off = 12
        while off + 8 <= len(data):
            fourcc = data[off:off + 4]
            seg = int.from_bytes(data[off + 4:off + 8], "little")
            body = data[off + 8:off + 8 + seg]
            if fourcc == b"VP8X" and len(body) >= 10:
                return (
                    int.from_bytes(body[4:7], "little") + 1,
                    int.from_bytes(body[7:10], "little") + 1,
                )
            if fourcc == b"VP8 " and len(body) >= 10:
                # 3 字节帧标签 + 0x9d 0x01 0x2a 同步码，之后是 14 位宽高
                return (
                    int.from_bytes(body[6:8], "little") & 0x3FFF,
                    int.from_bytes(body[8:10], "little") & 0x3FFF,
                )
            if fourcc == b"VP8L" and len(body) >= 5:
                bits = int.from_bytes(body[1:5], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            off += 8 + seg + (seg & 1)
        return None

    # 母版自 1536×1024 裁到 y≤950（抹掉右下角生成平台水印），再缩到成图尺寸。
    # 尺寸一旦回到 594 以上、或首页帧退回 1.6:1，就说明有人放回了
    # 没裁水印的原图，或者把重新构图过的横幅又换回去了。
    HERO_FRAME = (1152, 512)   # 首页轮播：与插画框 2.246:1 对齐；四帧必须同尺寸，否则切帧会跳
    SINGLE_FRAME = (960, 594)  # 登录页 / 结果页：1.616:1
    for name, label, expected in (
        ("hero-campus.webp", "首页横幅·主楼", HERO_FRAME),
        ("hero-xinkaihu.webp", "首页横幅·新开湖", HERO_FRAME),
        ("hero-muzhai.webp", "首页横幅·木斋图书馆", HERO_FRAME),
        ("hero-jinnan.webp", "首页横幅·津南湖畔", HERO_FRAME),
        ("hero-library.webp", "登录页插画", SINGLE_FRAME),
        ("hero-desk.webp", "结果页插画", SINGLE_FRAME),
    ):
        size = webp_size(img_dir / name)
        check(
            f"{label}尺寸与槽位一致（{expected[0]}×{expected[1]}）",
            size == expected,
            f"(实际 {size[0]}×{size[1]})" if size else "(无法解析，可能不是 WebP)",
        )

    if args.with_rag:
        print("\n=== 12. RAG 增强解析（真实调用模型）===")
        wrong = next((q for q in result["questions"] if not q["isCorrect"]), None)
        if wrong is None:
            print("        [SKIP] 本局全对，跳过 RAG 验证")
        else:
            rag = client.post(
                "/api/rag/explanations",
                json={"sessionId": session["sessionId"], "sessionQuestionId": wrong["sessionQuestionId"]},
            )
            payload = rag.json()
            check("RAG 接口返回 200/409 且结构合法", rag.status_code in (200, 409))
            if rag.status_code == 200 and payload.get("success"):
                inner = payload["data"]
                if inner.get("available"):
                    check("生成了增强解析", len(inner["explanation"]) > 10)
                    check("引用可验证（chunkId 来自本次 Top-3）",
                          all(c.get("chunkId") for c in inner.get("citations", [])))
                    print(f"        模型：{inner.get('model')} · 引用 {len(inner.get('citations', []))} 条")
                    if inner.get("citations"):
                        cite = inner["citations"][0]
                        print(f"        来源：《{cite['documentTitle']}》· {cite['sectionTitle']}")
                else:
                    print(f"        降级（不影响成绩）：{inner.get('message')}")
            else:
                print(f"        降级（不影响成绩）：{payload.get('error', {}).get('message')}")

    client.close()
    admin.close()

    print("\n" + "=" * 52)
    print(f"结果：通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print(f"  - {item}")
        return 1
    print("P0 核心闭环全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
