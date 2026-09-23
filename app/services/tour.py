"""新手指引（荷宝导览）：步骤定义与页面上下文。

设计取舍
--------
1. **步骤写在服务端，不写在前端 JS 里**。文案是产品内容，要能被 pytest
   与冒烟测试直接读到；几何计算（高亮框、卡片定位）才交给浏览器。
   前端只拿到一份 JSON，按「本页是否存在该锚点」过滤后逐条呈现。

2. **每个目标用 `data-tour` 锚点，不写死 DOM 结构或类名**。
   页面改版只要锚点还在，指引就不会错位；某个锚点整体消失时，
   那一步自动跳过（`optional` 只是给人和测试看的标注，
   判定逻辑始终是「准星找不到就跳过」）。

3. **指引可以跨页复用**。同一份定义在首页会展开 8 步、在知识库页
   只剩 5 步 —— 因为其它步骤的锚点在这页不存在。教师现场演示时
   可以在任意页面从账户菜单重开指引，看到的都是「这一页能做什么」。

4. **开场即记录**。是否看过由服务端 `users.tour_seen` 落库（跟着账号走，
   换浏览器也认得），localStorage 只做「下次进首页别闪一下」的镜像。
   记的是「展示过」而不是「学完了」：中途关掉页面的人，下次不该被
   同一份指引再拦一次。
"""

from __future__ import annotations

from typing import Any

#: 步骤定义的版本。改动文案或顺序时递增，前端的 localStorage 键随之变化，
#: 老用户会重新看到一次新版本 —— 免得改了内容却没人再看见。
TOUR_VERSION = "v1"

MASCOT_NAME = "荷宝"

#: 形象署名与出处。原型是南开大学官微学生团队自主设计的原创 IP
#: （大名「紫菡」，昵称「荷宝」，2024 年建校 105 周年随官方表情包上线，
#: 见澎湃新闻《气氛组就位！「荷宝」上线为南开庆生》）。本项目另绘了一版
#: 用于功能导览，署名随指引卡片一起展示；卡片上只放一行，完整说明见 README。
MASCOT_CREDIT = (
    "荷宝 · 本项目自绘导览形象；原型：南开大学官微学生团队原创 IP「紫菡」"
)

#: 允许出现的姿势，与 _mascot.html 的四个分支一一对应
MASCOT_POSES = ("wave", "point", "think", "cheer")

#: 允许出现的相对位置提示。都只是「优先」，前端在放不下时会自动改边。
TOUR_PLACEMENTS = ("center", "top", "bottom", "left", "right")


def _step(**kwargs: Any) -> dict[str, Any]:
    step = {
        "key": "",
        "title": "",
        "body": "",
        "target": "",
        "pose": "point",
        "placement": "bottom",
        "optional": False,
    }
    step.update(kwargs)
    return step


#: 步骤表。顺序即呈现顺序；`target` 为空表示居中的「无目标」步骤。
TOUR_STEPS: list[dict[str, Any]] = [
    _step(
        key="welcome",
        title="你好，我是荷宝",
        body=(
            "下面我用几步带你把这个站点走一遍：去哪儿开一局、成绩在哪里看、"
            "校史资料去哪里查。中途随时可以点「跳过」，之后从右上角账户菜单里"
            "还能重新打开这份指引。"
        ),
        target="",
        pose="wave",
        placement="center",
    ),
    _step(
        key="dashboard-start",
        title="先挑一局题",
        body=(
            "题量、分类、难度三样都可以先选。右边的「可用题量」会跟着你的条件实时变化，"
            "凑不满 5 题时会变成橙色，提醒你换个条件。"
        ),
        target='[data-tour="start-form"]',
        pose="point",
        placement="bottom",
    ),
    _step(
        key="dashboard-board",
        title="四个数字看清战绩",
        body=(
            "累计测评、最高得分、平均正确率、排行榜名次。只统计已完成的会话，"
            "中途退出的那局不会算进来。"
        ),
        target='[data-tour="stat-board"]',
        pose="point",
        placement="left",
    ),
    _step(
        key="dashboard-level",
        title="研习等级与 SP",
        body=(
            "每局按得分与正确率折算成 SP，攒够就升级；连着几天来研习，"
            "还会累加连续天数。"
        ),
        target='[data-tour="level-card"]',
        pose="point",
        placement="left",
        optional=True,   # 关闭奖励机制时这页没有等级卡
    ),
    _step(
        key="dashboard-recent",
        title="回来看看错在哪",
        body=(
            "这里只放最近两局，要全部记录就点「历史成绩」。每一条都能进结果页，"
            "逐题看对错、正确答案与解析。"
        ),
        target='[data-tour="recent-card"]',
        pose="point",
        placement="top",
        optional=True,
    ),
    _step(
        key="nav-knowledge",
        title="答不出来？先逛知识库",
        body=(
            "「校史知识库」把时间轴、人物档案、文化标识与题卡都摊开给你看，"
            "考前翻一遍最快。下面还有历史成绩、排行榜与成就墙。"
        ),
        target='[data-tour="nav-knowledge"]',
        pose="point",
        placement="right",
    ),
    _step(
        key="kb-cards",
        title="卡片可以翻面",
        body=(
            "每张卡片正面是问题、背面是答案与出处，可以单张翻，"
            "也可以用上面这三个按钮一键全开、全部收起或重新洗牌。"
        ),
        target='[data-tour="kb-tools"]',
        pose="think",
        placement="bottom",
        optional=True,   # 只有知识库页有这个锚点
    ),
    _step(
        key="account",
        title="想再看一遍？",
        body=(
            "右上角账户菜单里就有「新手指引」，随时能重开这份导览；"
            "退出登录也在这里。"
        ),
        target='[data-tour="account-menu"]',
        pose="point",
        placement="bottom",
    ),
    _step(
        key="done",
        title="就这些，去玩吧！",
        body=(
            "答题时可以用键盘（A–D 选选项、Enter 下一题）。超时不会额外扣分，"
            "已经答对的题照常判对。"
        ),
        target="",
        pose="cheer",
        placement="center",
    ),
]

#: 目标选择器 → 说明。仅用于文档与测试，帮助后续维护者知道锚点该挂在哪。
TOUR_ANCHORS: dict[str, str] = {
    '[data-tour="start-form"]': "首页「开始新一局」表单",
    '[data-tour="stat-board"]': "首页四格统计看板",
    '[data-tour="level-card"]': "首页研习等级卡",
    '[data-tour="recent-card"]': "首页「最近成绩」卡",
    '[data-tour="nav-knowledge"]': "侧栏「校史知识库」入口",
    '[data-tour="kb-tools"]': "知识库卡片工具条（洗牌 / 全开 / 收起）",
    '[data-tour="account-menu"]': "顶栏账户菜单（含重开指引）",
}


def page_payload(*, seen: bool, auto_start: bool) -> dict[str, Any]:
    """给模板的完整指引数据（渲染成一段 JSON，前端只做呈现）。"""
    return {
        "version": TOUR_VERSION,
        "mascot": MASCOT_NAME,
        "credit": MASCOT_CREDIT,
        "seen": seen,
        "autoStart": auto_start,
        "steps": TOUR_STEPS,
    }


def step_keys() -> list[str]:
    return [step["key"] for step in TOUR_STEPS]
