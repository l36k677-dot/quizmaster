"""把 docs/*.md 渲染成可提交的 PDF（开发设计文档 / AI 辅助对话记录）。

为什么要自己写这一步：
  交付要求是 PDF，但仓库里只有 Markdown 源。用 pandoc 需要另装外部程序，
  而本机没有；浏览器是现成的（截图验证本来就在用 Chrome），
  于是走「Markdown -> 带打印样式的 HTML -> Chrome 无头打印」这条路：
  零外部依赖安装、字体走系统、产物文本可搜索、中文不会变豆腐块。

原理说明（为什么不用 CSS 硬凑分页）：
  `@page { size: A4; margin: ... }` 是浏览器打印支持的正式写法，
  比在 HTML 里塞分页 div 稳；标题用 `break-after: avoid` 避免落在页脚，
  表格/代码块用 `break-inside: avoid` 避免被拦腰截断。

用法：
    uv run python scripts/make_pdf.py docs/DESIGN.md "dist/开发设计文档.pdf"
可选：
    --author "刘珂" --student-id "2413979" --subtitle "..."
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# Chrome 的位置：先看环境变量，再试几个常见安装路径。
CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

PRINT_CSS = """
@page { size: A4; margin: 20mm 17mm 18mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  font-family: "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB",
               "Noto Sans CJK SC", "Source Han Sans SC", sans-serif;
  font-size: 10.5pt;
  line-height: 1.8;
  color: #24303a;
}
code, pre, kbd { font-family: Consolas, "Cascadia Mono", Menlo, monospace; }

/* ---- 封面 ---- */
.cover { height: 235mm; display: flex; flex-direction: column; justify-content: center; break-after: page; }
.cover .kicker { font-size: 10pt; letter-spacing: 3px; color: #8a6a33; margin-bottom: 14mm; }
.cover h1 { font-size: 23pt; line-height: 1.45; margin: 0 0 6mm; border: none; }
.cover .subtitle { font-size: 13pt; color: #4a5a68; margin-bottom: 22mm; }
.cover .meta { font-size: 10.5pt; color: #4a5a68; border-top: 1px solid #d8cfbb; padding-top: 5mm; }
.cover .meta div { margin: 1.4mm 0; }
.cover .meta b { color: #24303a; font-weight: 600; display: inline-block; min-width: 22mm; }

/* ---- 目录 ---- */
.toc-page { break-after: page; }
.toc-page h2 { font-size: 15pt; border-bottom: 2px solid #c99a3f; padding-bottom: 2mm; }
.toc { font-size: 10pt; }
.toc ul { list-style: none; padding-left: 0; margin: 0; }
.toc ul ul { padding-left: 7mm; }
.toc li { margin: 1.1mm 0; }
.toc a { color: #24303a; text-decoration: none; }
.toc > ul > li > a { font-weight: 600; }

/* ---- 正文 ---- */
h1, h2, h3, h4 { line-height: 1.5; break-after: avoid; }
h1 { font-size: 18pt; margin: 0 0 5mm; }
h2 {
  font-size: 14pt; margin: 9mm 0 3.5mm;
  padding-bottom: 1.6mm; border-bottom: 1.5px solid #c99a3f;
}
h3 { font-size: 11.5pt; margin: 6mm 0 2.5mm; color: #1d4d45; }
h4 { font-size: 10.5pt; margin: 4.5mm 0 2mm; color: #4a5a68; }
p { margin: 2.4mm 0; }
a { color: #1d4d45; text-decoration: none; border-bottom: 1px solid #c9d6d2; }
ul, ol { padding-left: 6.5mm; margin: 2.4mm 0; }
li { margin: 1mm 0; }

/* 表格：表头浅底 + 细线，长表允许跨页但单行不拆 */
table { border-collapse: collapse; width: 100%; margin: 3mm 0; font-size: 9.5pt; }
th, td { border: 1px solid #cfc6b2; padding: 1.8mm 2.4mm; text-align: left; vertical-align: top; }
th { background: #f6f1e6; font-weight: 600; }
tr { break-inside: avoid; }

pre {
  background: #f7f5f0; border: 1px solid #e2dbcb; border-left: 3px solid #c99a3f;
  border-radius: 2px; padding: 3mm 3.5mm; margin: 3mm 0;
  font-size: 9pt; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
  break-inside: avoid;
}
code { background: #f3efe6; padding: 0.3mm 1mm; border-radius: 2px; font-size: 9.2pt; }
pre code { background: none; padding: 0; font-size: 9pt; }

blockquote {
  margin: 3mm 0; padding: 2.4mm 4mm; background: #fbf8f1;
  border-left: 3px solid #b8763c; color: #4a5a68; break-inside: avoid;
}
blockquote p { margin: 1.2mm 0; }
hr { border: none; border-top: 1px solid #e2dbcb; margin: 6mm 0; }

/* 截图：整宽 + 图注，一张不要被分页拆开 */
figure { margin: 5mm 0; break-inside: avoid; }
figure img { width: 100%; border: 1px solid #ded5c3; border-radius: 1mm; }
figcaption { margin-top: 1.6mm; font-size: 9pt; color: #5b6a76; line-height: 1.6; }
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>{title}</title>
<style>{css}</style>
</head><body>
<section class="cover">
  <div class="kicker">{kicker}</div>
  <h1>{title}</h1>
  <div class="subtitle">{subtitle}</div>
  <div class="meta">{meta}</div>
</section>
{toc_block}
<article class="doc">
{body}
</article>
</body></html>
"""


def find_chrome() -> str | None:
    for cand in CHROME_CANDIDATES:
        if cand and pathlib.Path(cand).exists():
            return cand
    found = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    return found


def first_heading(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return m.group(1).strip() if m else None


def strip_first_heading(text: str) -> str:
    """封面已经有标题了，正文里那个重复的一级标题要去掉。

    只在它确实是全文第一个非空块时删 —— 否则会误删正文中段的标题。
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^#\s+", line):
            return "\n".join(lines[:i] + lines[i + 1:])
        break
    return text


def split_title(heading: str) -> tuple[str, str]:
    """`开发设计文档 · QuizMaster …` 这类标题按「 · 」拆成主副标题。

    整串放封面上会撑到两行还压不住边；拆开后主标题一行、副标题一行，
    也顺手把「文档类型」和「项目名」分了层。
    """
    if " · " in heading:
        head, _, rest = heading.partition(" · ")
        return head.strip(), rest.strip()
    return heading.strip(), ""


def build_html(md_text: str, *, title: str, subtitle: str, kicker: str,
               author: str, student_id: str, date_text: str) -> str:
    import markdown

    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3", "anchorlink": False}},
    )
    body = md.convert(strip_first_heading(md_text))
    toc = getattr(md, "toc", "")

    rows = []
    if author:
        rows.append(f"<div><b>作者</b>{html.escape(author)}</div>")
    if student_id:
        rows.append(f"<div><b>学号</b>{html.escape(student_id)}</div>")
    rows.append(f"<div><b>日期</b>{html.escape(date_text)}</div>")
    meta = "\n  ".join(rows)

    toc_block = (
        f'<section class="toc-page"><h2>目录</h2><nav class="toc">{toc}</nav></section>'
        if toc else ""
    )
    return HTML_TEMPLATE.format(
        title=html.escape(title), subtitle=html.escape(subtitle),
        kicker=html.escape(kicker), css=PRINT_CSS, meta=meta,
        toc_block=toc_block, body=body,
    )


def render_pdf(chrome: str, html_path: pathlib.Path, pdf_path: pathlib.Path) -> None:
    # Chrome 的工作目录和我们不同，--print-to-pdf 必须是绝对路径，
    # 否则会报「系统找不到指定的路径」而退出码仍是 0。
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    profile = pathlib.Path(tempfile.mkdtemp(prefix="qm-pdf-"))
    try:
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            "--virtual-time-budget=8000",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0 or not pdf_path.exists():
            tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-4:]
            raise RuntimeError(f"Chrome 打印失败（退出码 {proc.returncode}）：{tail}")
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Markdown -> PDF（Chrome 无头打印）")
    ap.add_argument("source", help="输入 Markdown 路径")
    ap.add_argument("output", help="输出 PDF 路径")
    ap.add_argument("--title", default="", help="封面标题（默认取文档首个一级标题）")
    ap.add_argument("--subtitle", default="", help="封面副标题")
    ap.add_argument("--kicker", default="南开大学 · 综合实践项目", help="封面眉标")
    ap.add_argument("--author", default="", help="作者")
    ap.add_argument("--student-id", default="", help="学号")
    ap.add_argument("--date", default="", help="日期（默认今天）")
    args = ap.parse_args(argv)

    src = pathlib.Path(args.source)
    if not src.exists():
        print(f"找不到源文件：{src}", file=sys.stderr)
        return 1

    heading = args.title or first_heading(src.read_text(encoding="utf-8")) or src.stem
    default_title, default_sub = split_title(heading)
    title = args.title or default_title
    subtitle = args.subtitle or default_sub
    chrome = find_chrome()
    if not chrome:
        print("找不到 Chrome。可用环境变量 CHROME_PATH 指定可执行文件。", file=sys.stderr)
        return 1

    html_text = build_html(
        src.read_text(encoding="utf-8"),
        title=title, subtitle=subtitle, kicker=args.kicker,
        author=args.author, student_id=args.student_id,
        date_text=args.date or dt.date.today().isoformat(),
    )

    # HTML 与它引用的截图镜像到临时目录再渲染：文档里的 `screenshots/xx.png`
    # 是相对路径，HTML 必须与截图同构放置才对得上；放临时目录则工程树零残留。
    out = pathlib.Path(args.output)
    with tempfile.TemporaryDirectory(prefix="qm-pdf-html-") as tmpdir:
        tmp = pathlib.Path(tmpdir)
        shots = src.parent / "screenshots"
        if shots.is_dir():
            shutil.copytree(shots, tmp / "screenshots")
        html_path = tmp / f"{src.stem}.html"
        html_path.write_text(html_text, encoding="utf-8")
        render_pdf(chrome, html_path, out)

    print(f"{src}  ->  {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
