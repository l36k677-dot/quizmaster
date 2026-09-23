"""按验收要求的目录格式，把工程组装成可提交的压缩包。

要求的结构（口径来自 2026-09-20 面授纪要：材料清单 = 全部代码 + README +
开发设计文档 + AI 对话记录 + **演示视频**，打包为一个「学号+姓名」命名的压缩包）：

    2413979_刘珂.zip
      ├── 代码/
      ├── README.md
      ├── 开发设计文档.pdf
      ├── AI辅助对话记录.pdf
      ├── 演示视频.mp4      <- 用 --video 传进来；不传则 README 如实写「未随包提供」
      └── 选题说明.txt      <- 用 --extra 传进来（可选，放题目说明 / 会议链接这类补充材料）

本脚本负责前四项：把工程复制成 `代码/`、把两份 Markdown 打印成 PDF、写一份面向评审的
根 README，最后压成 `dist/学号_姓名.zip`。**视频由本人录制**，录好后用 `--video` 指路，
脚本会把它放进包内并同步 README 里的说明；还没录也能打，只是这一项会缺且如实标注。

用法：
    uv run python scripts/build_submission.py --id 2413979 --name 刘珂 --video D:/演示视频.mp4
    uv run python scripts/build_submission.py --id 2413979 --name 刘珂 --extra 选题说明.txt
    uv run python scripts/build_submission.py --id 2413979 --name 刘珂   # 视频还没录
    uv run python scripts/build_submission.py                # 占位名 学号_姓名

不打包数据库是默认行为：`data/*.db` 属于运行时产物，且里面是真实账号的答题记录。
不带上它，包在别人机器上照样跑得起来 —— `start.bat` 检测到没有库会自动初始化。
确实需要连库一起交时再加 `--with-db`。

视频文件（`VIDEO_SUFFIXES`）**一律不进 `代码/`**：材料清单里「演示视频」与 `代码/` 是同层级的
两个独立条目，视频只能由 `--video` 放到顶层。把视频留在工程目录里会被整份复制，包体积翻倍。
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import make_pdf  # noqa: E402  （同目录脚本，用于生成交付 PDF）

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 每次打包成功后写进 stage 目录的标记文件：只有带这个标记的目录才允许被下次打包清空。
# 这样即使 --out 传错到别的目录，脚本也只会拒绝、不会误删用户自己的文件。
STAGE_MARKER = ".build_submission_stage"

# 目录名：整个跳过后代
EXCLUDE_DIRS = {
    ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".git", ".github", "node_modules", "backups", ".workbuddy", "logs",
    ".cache-chrome", ".uv-cache", "dist",
}

# 文件名：
#   .env 里有真实模型密钥，绝不入包（.env.example 照常带上）
#   *.db* 是运行时数据库
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db", "desktop.ini"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".db-journal")

# 视频一律不进 `代码/`：材料清单里「演示视频」与 `代码/` 是同一层级的两个独立条目，
# 视频混进工程树只会让包体积翻倍（实测在 quizmaster/ 下放一份 82 MB 的视频，
# 打包后 zip 从 12.6 MB 涨到 94 MB，且内容重复）。视频只允许由 --video 放进顶层。
VIDEO_SUFFIXES = (
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".m4v", ".webm", ".mpg", ".mpeg", ".ts",
)


def is_excluded(rel: pathlib.Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    name = rel.name
    if name in EXCLUDE_FILES:
        return True
    if name.endswith(EXCLUDE_SUFFIXES):
        return True
    # 视频只允许作为顶层交付项存在，绝不能被卷进 `代码/`
    if name.lower().endswith(VIDEO_SUFFIXES):
        return True
    # make_pdf 的中间产物：.DESIGN.build.html 之类
    if name.startswith(".") and name.endswith(".build.html"):
        return True
    return False


README_TEMPLATE = """# QuizMaster · 南开校史在线知识竞答系统

> 本文件是本提交包的入口说明。**工程自身的详细文档在 [`代码/README.md`](代码/README.md)**，
> 那份讲的是设计取舍与实现细节；这份只讲「怎么跑起来」和「包里有什么」。

## 一、包里有什么

| 条目 | 内容 |
|---|---|
| `代码/` | 完整可运行工程（FastAPI + Jinja2 + SQLAlchemy + SQLite），含源码、种子数据、测试与工程文档 |
| `README.md` | 本文件：运行方式与提交对照 |
| `开发设计文档.pdf` | 背景与范围、角色权限、UI 结构、视觉设计、数据模型、API、状态机、判分、奖励、RAG、测试与截图附录 |
| `AI辅助对话记录.pdf` | 与 AI 协作的轮次记录、人工否决 AI 建议的清单、AI 代码审查清单与验证证据 |
| {video_row} |
{extra_row}

## 二、选题与验收要求对照

**选题：第 17 题「在线知识竞答游戏」。**

**选题背景** — 知识竞答将题库、随机出题、计时、判分和排行榜结合起来，既有完整交互流程，又容易现场验证。题面要求实现一个**可完整玩一局**的竞答应用。

**项目目标** — 完成题库管理、游戏开始、连续答题、计时计分和结果统计。

| 题面要求 | 本项目怎么做 | 现场怎么核 |
|---|---|---|
| **必做功能** · 题库管理 | 管理端增删改查 + 分类 / 难度 / 题型 / 启用状态 | 用 `admin / QuizAdmin@2026` 登录改一道题，回到历史成绩，旧分数不动 |
| **必做功能** · 随机或规则出题 | 按题量 / 分类 / 难度筛池后随机抽取，同一局内不重复 | 连开两局，题目组合不同、局内不重题 |
| **必做功能** · 答题交互 | 进度格 + 答题卡 + 键盘 A–D / 方向键 | 开一局答到底；少答一题也能交卷 |
| **必做功能** · 单题或整局计时 | 单题 60 秒，整局 = 单题 × 题数，以服务端时间为准 | 等倒计时归零，看它自动结算为「超时」 |
| **必做功能** · 自动计分 | 服务端纯函数按题库**快照**判分，客户端传的分数一律忽略 | 结果页给出「正确数 × 100 ÷ 题数」的可复核算式 |
| **必做功能** · 成绩历史记录 | 分页历史 + 状态筛选 + 汇总统计（完赛局数 / 最佳分 / 平均正确率 / 我的名次） | 答完后进「历史成绩」；重启服务再查一次仍在 |
| **关键业务规则** · 按标准答案计算 | 与快照中的标准答案比对，**改题库不影响历史成绩** | 改掉某题的正确答案，旧成绩不变 |
| **关键业务规则** · 超时按预设规则处理 | 到期惰性结算为超时；已存答案照判、未作答按错；用时按截止封顶；留 3 秒网络宽限；不额外扣分 | 卡着倒计时提交，仍能正常出分 |
| **统计 / 可视化要求** | **三项全做**：排行榜 + 正确率 + 历史成绩 | 首页排行榜、结果页正确率、历史成绩页 |
| **异常场景要求** | 超时 / 重复答题 / 空题库 / 非法选项，各有独立错误码与提示 | 题库只留几道题时开考，会明确拒绝而不缩水成局 |
| **可选 AI 扩展** | **1 / 3 已做**：AI 解释答案（RAG 增强解析）。「自动生成题目」「按错题调整难度」**未做** | 结果页「资料增强解析」；未配密钥时自动降级为标准解析 |

**对功能完成的印证（在线知识竞答游戏）** — 系统跑的不是静态页面、也不是固定演示：抽到的题、判出的分、写下的成绩都出自真实数据库。三条印证路径：① 按 `代码/docs/TEST_REPORT.md` §6 的现场手册逐步操作；② 跑 `pytest -q`（104 项）与 `scripts/smoke_test.py`（84 项真实 HTTP 流程）；③ 答完一局后查库 —— `sessions` / `answers` / `scores` 三张表同时留下记录，重启服务后仍可查。

第二部分「所有项目统一完成要求」的逐条对照见 `代码/docs/TEST_REPORT.md` §5.1。

## 三、怎么跑起来

**依赖只需要 Python 3.11+，用不用 uv 都可以。** 本包附带 `代码/requirements.txt`，
里面锁定的版本与 `uv.lock` 一致 —— 两条路径装出来的环境等价，功能完全一样。

- 装了 [`uv`](https://docs.astral.sh/uv/)：一条 `uv sync` 搞定（它还会自动准备合适的 Python）；
- 只装了 Python：`python -m venv` + `pip install -r requirements.txt` 同样可以。

```
cd 代码

# Windows：双击 start.bat
# macOS / Linux：
bash start.sh
```

`start.bat` / `start.sh` 会**自动判断这台机器走哪条路**：有 `uv` 就 `uv sync`，
没有就退回 `python -m venv` + `pip install -r requirements.txt`；
然后检查依赖 → 没有数据库就初始化 → 起服务，并**轮询 `/healthz` 就绪后**再打开浏览器，
不会开出空白页。（两者都没有时会给出安装指引并退出，不会静默失败。）

手工等价的两组命令，任选一组：

```
# ---- 有 uv ----
uv sync
uv run python scripts/init_db.py      # 建表 + 导入 80 道题 / 成就 / 讲义索引
uv run python run.py                  # http://127.0.0.1:8000

# ---- 没有 uv，只有 Python 3.11+ ----
python -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt      # Windows
.venv/bin/python -m pip install -r requirements.txt              # macOS / Linux
.venv\\Scripts\\python.exe scripts/init_db.py
.venv\\Scripts\\python.exe run.py
```

> 网络慢或装不上时加国内镜像：
> `... -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`

浏览器打开 <http://127.0.0.1:8000>，**首次使用请在登录页点「注册」创建自己的账号** ——
成绩、SP、成就都绑定到账号，每个账号看到的都是自己的真实数据，没有预置的演示账号或示例成绩。

## 四、账号说明

- **学生账号**：自行注册，一人一号。
- **管理员账号**：初始化时写入一个 `admin`（初始口令 `QuizAdmin@2026`），用于题库 / 资料库维护，
  以及成就与 SP 的重算。**正式部署前请务必更换该口令。**
- 未随包提供数据库文件，因此首次启动时库是空的；这是有意的 —— 避免把开发期的账号与答题记录一并交出去。

## 五、功能开关与环境变量

复制 `代码/.env.example` 为 `代码/.env` 后按需修改（`.env` 不入包，也不要提交真实密钥）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `development` | 仅 development 下允许短时限演示参数生效 |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `SESSION_SECRET` | 开发占位值 | 会话 Cookie 签名密钥，**生产必须替换为长随机串** |
| `QUESTION_TIME_LIMIT_SECONDS` | `60` | 每题答题时限（秒） |
| `REWARD_ENABLED` | `true` | 积分 / 等级 / 成就总开关 |
| `RAG_ENABLED` | `true` | 「资料增强解析」总开关 |
| `AI_API_KEY` | 空 | 模型密钥。**留空时 RAG 自动降级为题库自带的标准解析，功能不报错** |

> 因此**不配密钥也能完整跑通全流程**，只是结果页的「资料增强解析」会走降级路径。

## 六、自检

```
cd 代码
uv run python -m pytest -q                       # 单元与集成测试
uv run python scripts/smoke_test.py              # HTTP 端到端冒烟（不调模型）
uv run python scripts/smoke_test.py --with-rag   # 加上真实模型调用（需要 AI_API_KEY）
```

> 没有 uv 时，把上面的 `uv run python` 换成 `.venv\\Scripts\\python.exe`（Windows）
> 或 `.venv/bin/python`（macOS / Linux）即可，参数不用改。

## 七、已知边界

- 登录与作答依赖 JavaScript（要发请求）；**其余页面均为服务端渲染，关掉 JS 照样看得到真实数据**。
- RAG 使用稀疏检索（BM25）+ 讲义语料，无向量库；未配密钥时自动降级。
- 数据库为单文件 SQLite（WAL 模式），面向单机演示，不做并发写入优化。

## 八、未来修改方向

- **增加闯关系统** —— 把「自选题量、随机抽一局」扩展为分章节、分难度的关卡链路：逐关解锁、连对加成、每关独立结算。数据模型只需在 `sessions` 之上叠一层闯关进度，现有判分与快照逻辑可原样复用。
- **UI 界面优化** —— 结果页的逐题回放与知识库的长文阅读仍有提升空间（错题对比视图、正确率走势图），移动端窄屏的信息密度也可再压一档。
- **强化奖励机制** —— 现有 SP / 等级 / 成就只「累积」不「消耗」，后续可加入连续打卡、日榜 / 周榜与成就组合，让奖励不只反映历史总量，也能反映近期活跃度。

## 九、提交信息

| 项 | 值 |
|---|---|
| 压缩包 | `{pkg_name}.zip`（本文件即包根目录下的 `README.md`） |
| 材料清单 | `代码/` · `README.md` · `开发设计文档.pdf` · `AI辅助对话记录.pdf` · `演示视频` |
| 截止时间 | **2026-10-30 24:00 前** |
| 提交邮箱 | `2120250752@mail.nankai.edu.cn` |

---

打包信息：{pkg_name} · 生成于 {date}
"""


def copy_code_tree(dest: pathlib.Path, *, with_db: bool) -> tuple[int, int]:
    """把工程复制成 `代码/`，返回 (文件数, 字节数)。"""
    count = 0
    total = 0
    for src in sorted(PROJECT_ROOT.rglob("*")):
        rel = src.relative_to(PROJECT_ROOT)
        if is_excluded(rel):
            continue
        if not with_db and rel.parts[:1] == ("data",) and src.suffix == ".db":
            continue
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        count += 1
        total += src.stat().st_size
    return count, total


def build_docs(stage: pathlib.Path, author: str, student_id: str) -> list[pathlib.Path]:
    """把两份 Markdown 打印成 PDF，返回产物路径。"""
    chrome = make_pdf.find_chrome()
    if not chrome:
        raise RuntimeError("找不到 Chrome，无法生成 PDF（可用 CHROME_PATH 指定）")

    jobs = [
        ("docs/DESIGN.md", "开发设计文档.pdf", ""),
        ("docs/AI_ASSISTED_DEVELOPMENT.md", "AI辅助对话记录.pdf", ""),
    ]
    made = []
    # 中间 HTML 与它引用的截图都镜像到临时目录再渲染：
    # 文档里写的是 `screenshots/xx.png` 这类相对路径，HTML 必须与截图同构放置才对得上；
    # 放到临时目录而不是源码目录，工程树里就不会留下任何中间产物，也无需事后删除。
    with tempfile.TemporaryDirectory(prefix="quizmaster-doc-") as tmpdir:
        tmp_docs = pathlib.Path(tmpdir) / "docs"
        tmp_docs.mkdir(parents=True)
        shots = PROJECT_ROOT / "docs" / "screenshots"
        if shots.is_dir():
            shutil.copytree(shots, tmp_docs / "screenshots")

        for rel, out_name, subtitle in jobs:
            src = PROJECT_ROOT / rel
            if not src.exists():
                raise FileNotFoundError(f"缺少源文档：{src}")
            text = src.read_text(encoding="utf-8")
            title, default_sub = make_pdf.split_title(make_pdf.first_heading(text) or src.stem)
            html_path = tmp_docs / f"{src.stem}.html"
            html_path.write_text(
                make_pdf.build_html(
                    text, title=title, subtitle=subtitle or default_sub,
                    kicker="南开大学 · 综合实践项目",
                    author=author, student_id=student_id,
                    date_text=dt.date.today().isoformat(),
                ),
                encoding="utf-8",
            )
            out = stage / out_name
            make_pdf.render_pdf(chrome, html_path, out)
            made.append(out)
            print(f"    {out_name}  {out.stat().st_size / 1024:.0f} KB")
    return made


def _human(n: int) -> str:
    """把字节数写成人看的大小；包里多了一个可能上百 MB 的视频，KB 已经不好读了。"""
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024:.0f} KB"


def make_zip(stage: pathlib.Path, zip_path: pathlib.Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file() and f.name != STAGE_MARKER:
                zf.write(f, f.relative_to(stage.parent))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="组装并打包验收提交物")
    ap.add_argument("--id", dest="student_id", default="", help="学号")
    ap.add_argument("--name", dest="student_name", default="", help="姓名")
    ap.add_argument("--out", default="", help="输出目录（默认 <项目同级>/dist）")
    ap.add_argument("--with-db", action="store_true", help="连数据库一起打包（默认不带）")
    ap.add_argument("--no-pdf", action="store_true", help="跳过 PDF 生成（只做代码打包）")
    ap.add_argument("--video", default="", help="演示视频路径；传入即随包提交（不传则包里缺此项）")
    ap.add_argument(
        "--extra", action="append", default=[], metavar="PATH",
        help="额外随包文件（可重复）：原样放到压缩包顶层，用于题目说明 / 会议链接这类补充材料",
    )
    args = ap.parse_args(argv)

    pkg_name = f"{args.student_id or '学号'}_{args.student_name or '姓名'}"
    out_root = pathlib.Path(args.out).resolve() if args.out else PROJECT_ROOT.parent / "dist"
    stage = out_root / pkg_name
    zip_path = out_root / f"{pkg_name}.zip"

    video_src: pathlib.Path | None = None
    if args.video:
        video_src = pathlib.Path(args.video).expanduser().resolve()
        # 先校验再动 stage：路径写错时应该什么都不做就退出，而不是清空上一次的产物
        if not video_src.is_file():
            print(f"找不到演示视频：{video_src}", file=sys.stderr)
            return 2

    extras: list[pathlib.Path] = []
    for raw in args.extra:
        p = pathlib.Path(raw).expanduser().resolve()
        if not p.is_file():
            print(f"找不到附加文件：{p}", file=sys.stderr)
            return 2
        extras.append(p)

    if stage.exists():
        if not (stage / STAGE_MARKER).exists():
            print(
                f"拒绝清空已有目录：{stage}\n"
                f"  该目录没有 {STAGE_MARKER} 标记，不是本脚本的产物；\n"
                "  请换一个 --out，或手动确认后再删除，脚本不会替你动它。",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / STAGE_MARKER).write_text(
        "本目录由 scripts/build_submission.py 生成，可被下一次打包覆盖。\n",
        encoding="utf-8",
    )
    print(f"打包目录：{stage}")

    print("\n[1/6] 复制工程 -> 代码/")
    count, size = copy_code_tree(stage / "代码", with_db=args.with_db)
    print(f"    {count} 个文件，{size / 1024 / 1024:.2f} MB"
          f"{'' if args.with_db else '（已排除 .env / data/*.db / .venv / backups）'}")

    print("\n[2/6] 打印 PDF")
    if args.no_pdf:
        print("    已跳过（--no-pdf）")
    else:
        build_docs(stage, args.student_name, args.student_id)

    print("\n[3/6] 放入演示视频")
    video_name = ""
    if video_src is None:
        print("    未提供 --video，包内不含视频（README 会如实标注）")
    else:
        # 统一改名为「演示视频.<原扩展名>」：材料清单里的这一项就该叫这个名字，便于评审逐项对照
        video_name = f"演示视频{video_src.suffix.lower() or '.mp4'}"
        shutil.copy2(video_src, stage / video_name)
        print(f"    {video_src.name} -> {video_name}"
              f"  {(stage / video_name).stat().st_size / 1024 / 1024:.1f} MB")

    print("\n[4/6] 放入附加材料")
    extra_row = ""
    if not extras:
        print("    未提供 --extra，无附加材料")
    else:
        for p in extras:
            shutil.copy2(p, stage / p.name)
            print(f"    {p.name}  {_human(p.stat().st_size)}")
        extra_row = "| {} | 随包附加材料（选题说明 / 会议链接） |".format(
            "、".join(f"`{p.name}`" for p in extras)
        )

    print("\n[5/6] 写提交说明 README.md")
    video_row = (
        f"`{video_name}` | 完整演示闭环（登录 → 答题 → 结果 → 题库管理），"
        "含超时自动交卷与 RAG 降级说明"
        if video_name
        else "`演示视频` | **未随包提供**（尚未录制）；录好后加 `--video <路径>` 重新打包即可入包"
    )
    (stage / "README.md").write_text(
        README_TEMPLATE.replace("{pkg_name}", pkg_name)
        .replace("{video_row}", video_row)
        .replace("{extra_row}", extra_row)
        .replace("{date}", dt.date.today().isoformat()),
        encoding="utf-8",
    )

    print("\n[6/6] 压缩")
    make_zip(stage, zip_path)

    print("\n=== 交付清单 ===")
    for item in sorted(stage.iterdir()):
        if item.name == STAGE_MARKER:
            continue
        if item.is_dir():
            n = sum(1 for _ in item.rglob("*") if _.is_file())
            print(f"  {item.name + '/':<26} {n:>4} 个文件")
        else:
            print(f"  {item.name:<26} {_human(item.stat().st_size):>9}")
    print(f"\n  -> {zip_path}  ({zip_path.stat().st_size / 1024 / 1024:.2f} MB)")
    if video_src is None:
        print("\n材料清单还差一项：**演示视频**。录好后重跑一次（本机需前台运行）：")
        print(
            f"  uv run python scripts/build_submission.py --id {args.student_id or '学号'} "
            f"--name {args.student_name or '姓名'} --video <视频路径>"
        )
        print(f"  交出去的是重新生成的 zip，不是 {stage.name}/ 这个目录。")
    else:
        extra_note = f"，另有附加材料 {len(extras)} 份" if extras else ""
        print("\n材料清单 5 项已齐：代码 / README / 开发设计文档 / AI 对话记录 / 演示视频"
              f"{extra_note}。")
        print("  发送前自查邮箱附件大小上限；视频过大就先压缩，或按纪要允许的「链接」方式补上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
