#!/usr/bin/env bash
# QuizMaster · 南开校史知识竞答 —— 统一运行入口（Linux / macOS）
# 用法：bash start.sh
#
# 依赖安装方式：优先 uv，没有 uv 就退回 python + pip。
# 两条路径装出来的版本一致（requirements.txt 与 uv.lock 对齐），功能完全等价 ——
# 评审电脑上装没装 uv 都能跑起来。
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  QuizMaster · 南开校史知识竞答"
echo "  =================================="
echo "  统一运行入口：依赖检查 - 数据库初始化 - 启动服务"
echo

if command -v uv >/dev/null 2>&1; then
  echo "  [环境] 检测到 uv —— 用 uv 管理依赖"
  if [ ! -d ".venv" ]; then
    echo "  [1/3] 首次运行，正在安装依赖 ...（需要联网，约 1-2 分钟）"
    uv sync
  fi
  echo "  [1/3] 依赖环境已就绪"
  run_py() { uv run python "$@"; }
else
  py=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then py="$cand"; break; fi
  done
  if [ -z "$py" ]; then
    echo "  [错误] 这台机器上既没有 uv，也没有 Python。"
    echo
    echo "  装任意一个即可（推荐 uv，一条命令搞定 Python 环境）："
    echo "    uv     https://docs.astral.sh/uv/getting-started/installation/"
    echo "    Python https://www.python.org/downloads/"
    exit 1
  fi
  if ! "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "  [错误] Python 版本过低，本项目需要 3.11 及以上。当前：$("$py" -V 2>&1)"
    echo
    echo "  建议装 uv（它会自动准备合适的 Python，不用你管版本）："
    echo "    https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi

  echo "  [环境] 未检测到 uv —— 改用 $py + pip（等价方式）"

  if [ ! -d ".venv" ]; then
    echo "  [1/3] 首次运行，正在创建虚拟环境并安装依赖 ...（需要联网，约 1-2 分钟）"
    "$py" -m venv .venv
  fi

  # 虚拟环境里的解释器路径随平台而异：Linux/macOS 是 bin/，Windows（含 Git Bash / MSYS）是 Scripts/
  venv_py=""
  if [ -x ".venv/bin/python" ]; then
    venv_py=".venv/bin/python"
  elif [ -x ".venv/Scripts/python.exe" ]; then
    venv_py=".venv/Scripts/python.exe"
  fi
  if [ -z "$venv_py" ]; then
    echo "  [错误] 虚拟环境创建失败，请检查 Python 安装是否完整。"
    exit 1
  fi

  # 依赖只在没装过时装一次（重跑 start.sh 不必再联网）
  if [ ! -f ".venv/.qm-deps-ready" ]; then
    if ! "$venv_py" -m pip install -r requirements.txt; then
      echo
      echo "  [错误] pip 安装依赖失败，通常是网络问题。"
      echo
      echo "  换成国内镜像再试（阿里云 / 清华，二选一）："
      echo "    \"$venv_py\" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/"
      echo "    \"$venv_py\" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
      echo
      echo "  或者改用 uv（自带国内可用的下载源与重试）："
      echo "    https://docs.astral.sh/uv/getting-started/installation/"
      exit 1
    fi
    touch ".venv/.qm-deps-ready"
  fi

  echo "  [1/3] 依赖环境已就绪"
  run_py() { "$venv_py" "$@"; }
fi

if [ -f "data/quizmaster.db" ]; then
  echo "  [2/3] 数据库已存在，跳过初始化"
else
  echo "  [2/3] 首次运行，正在初始化数据库（题库 / 成就 / 讲义）..."
  run_py scripts/init_db.py
fi

echo "  [3/3] 启动服务 ... 浏览器将在几秒后自动打开"
echo
echo "  访问地址：http://127.0.0.1:8000"
echo "  首次使用请在登录页点击「注册」创建账号，成绩只属于该账号。"
echo "  按 Ctrl+C 停止服务。"
echo

# 后台等服务就绪再打开浏览器：直接 sleep 固定秒数，在慢机器上会打开一个空页面。
# 拿不到图形环境时静默跳过，不影响服务本身。
(
  i=0
  while [ "$i" -lt 40 ]; do
    if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
      break
    fi
    i=$((i + 1))
    sleep 0.5
  done
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://127.0.0.1:8000 >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open http://127.0.0.1:8000 >/dev/null 2>&1 || true
  fi
) &

run_py run.py
