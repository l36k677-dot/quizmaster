"""交付包组装规则的回归测试。

`scripts/build_submission.py` 是唯一产出交付物的代码，但它跑一次要打 PDF、压几十 MB 的包，
不适合塞进单测。这里只测**纯函数级的规则**：哪些文件该进 `代码/`、哪些绝不能进，
以及包内 README 是否如实声明了材料清单。

这些规则曾经被绕过一次：演示视频被放进了工程根目录，而 `代码/` 是整个工程的复制，
于是 82 MB 的视频先被复制进 `代码/`、再由 `--video` 在顶层放一份，zip 从 12.6 MB 涨到约 94 MB。
症状是「包变大」而不是报错，所以必须有断言守着。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import build_submission as bs  # noqa: E402


@pytest.mark.parametrize(
    "rel",
    [
        ".env",                       # 真实模型密钥
        "data/quiz.db",               # 运行时数据库
        "data/quiz.db-wal",           # SQLite WAL
        "__pycache__/x.pyc",
        ".venv/Lib/site.py",
        "backups/quiz-20260901.db",
        "docs/.DESIGN.build.html",    # make_pdf 的中间产物
    ],
)
def test_sensitive_and_runtime_files_are_excluded(rel: str) -> None:
    assert bs.is_excluded(pathlib.Path(rel)), f"{rel} 不该进交付包"


@pytest.mark.parametrize(
    "rel",
    [
        "演示视频.mp4",
        "代码讲解.mov",
        "assets/demo.MKV",            # 大小写混写也要挡住
        "video/clip.webm",
    ],
)
def test_video_files_never_enter_code_tree(rel: str) -> None:
    """视频是顶层交付项，不是工程源码 —— 它该进包，但不该进 `代码/`。"""
    assert bs.is_excluded(pathlib.Path(rel))


@pytest.mark.parametrize(
    "rel",
    [
        "app/main.py",
        "app/templates/base.html",
        "data/seed/questions.json",
        "docs/DESIGN.md",
        ".env.example",               # 模板要留，只有真实 .env 排除
        "README.md",
        "start.sh",
    ],
)
def test_source_files_are_kept(rel: str) -> None:
    assert not bs.is_excluded(pathlib.Path(rel)), f"{rel} 应该保留在交付包里"


def test_copy_code_tree_skips_video_db_and_env(tmp_path, monkeypatch) -> None:
    """端到端跑一遍复制：视频 / 数据库 / .env 都不该出现在 `代码/` 下。"""
    src = tmp_path / "project"
    (src / "app").mkdir(parents=True)
    (src / "data").mkdir()
    (src / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (src / "data" / "seed.json").write_text("{}\n", encoding="utf-8")
    (src / ".env").write_text("AI_API_KEY=secret\n", encoding="utf-8")
    (src / "data" / "quiz.db").write_bytes(b"SQLite format 3\x00")
    (src / "演示视频.mp4").write_bytes(b"\x00" * 1024)

    monkeypatch.setattr(bs, "PROJECT_ROOT", src)
    dest = tmp_path / "out" / "代码"
    count, _ = bs.copy_code_tree(dest, with_db=False)

    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert copied == ["app/main.py", "data/seed.json"], copied
    assert count == 2


def test_readme_template_declares_all_five_items() -> None:
    """包内 README 必须逐项对齐材料清单，并写明截止时间与投递邮箱。"""
    tpl = bs.README_TEMPLATE
    for item in ["代码/", "README.md", "开发设计文档.pdf", "AI辅助对话记录.pdf"]:
        assert item in tpl, f"README 模板缺条目：{item}"
    assert "{video_row}" in tpl, "视频条目应由打包时按实际情况填入"
    assert "2026-10-30 24:00" in tpl
    assert "2120250752@mail.nankai.edu.cn" in tpl
    assert "{pkg_name}" in tpl and "{date}" in tpl
