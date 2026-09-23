@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo   QuizMaster · 南开校史知识竞答
echo   ==================================
echo   统一运行入口：依赖检查 - 数据库初始化 - 启动服务
echo.

rem ============================================================
rem  依赖安装方式：优先 uv，没有 uv 就退回 python + pip
rem  两条路径装出来的版本一致（requirements.txt 与 uv.lock 对齐），
rem  功能完全等价 —— 评审电脑上装没装 uv 都能跑起来。
rem ============================================================

set "USE_UV="
set "PY="
set "RUNPY="

where uv >nul 2>nul
if not errorlevel 1 set "USE_UV=1"
if defined USE_UV goto :env_uv

rem ---- 没有 uv：找一个可用的 Python ----
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py"
)
if not defined PY goto :no_runtime

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :bad_version
goto :env_pip

rem ------------------------------------------------------------ uv 路径
:env_uv
echo   [环境] 检测到 uv —— 用 uv 管理依赖
set "RUNPY=uv run python"
if exist ".venv" goto :deps_ready
echo   [1/3] 首次运行，正在安装依赖 ... ^(需要联网，约 1-2 分钟^)
uv sync
if errorlevel 1 goto :uv_fail
goto :deps_ready

rem ------------------------------------------------------------ pip 路径
:env_pip
echo   [环境] 未检测到 uv —— 改用 %PY% + pip ^(等价方式^)
set "RUNPY=.venv\Scripts\python.exe"
if exist ".venv" goto :deps_ready
echo   [1/3] 首次运行，正在创建虚拟环境并安装依赖 ... ^(需要联网，约 1-2 分钟^)
%PY% -m venv .venv
if errorlevel 1 goto :deps_fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_fail
goto :deps_ready

rem ------------------------------------------------------------ 装好了，往下走
:deps_ready
echo   [1/3] 依赖环境已就绪

if exist "data\quizmaster.db" (
  echo   [2/3] 数据库已存在，跳过初始化
) else (
  echo   [2/3] 首次运行，正在初始化数据库 ^(题库 / 成就 / 讲义^) ...
  %RUNPY% scripts\init_db.py
)

echo   [3/3] 启动服务 ... 浏览器将在几秒后自动打开
echo.
echo   访问地址：http://127.0.0.1:8000
echo   首次使用请在登录页点击「注册」创建账号，成绩只属于该账号。
echo   按 Ctrl+C 停止服务。
echo.

start "" /min cmd /c "ping -n 5 127.0.0.1 >nul & start http://127.0.0.1:8000"

%RUNPY% run.py

echo.
echo   服务已停止。
pause
exit /b 0

rem ============================================================ 出错分支
:no_runtime
echo   [错误] 这台电脑上既没有 uv，也没有 Python。
echo.
echo   装任意一个即可（推荐 uv，一条命令搞定 Python 环境）：
echo     uv     https://docs.astral.sh/uv/getting-started/installation/
echo     Python https://www.python.org/downloads/   ^(安装时勾选 Add Python to PATH^)
echo.
pause
exit /b 1

:bad_version
echo   [错误] Python 版本过低，本项目需要 3.11 及以上。
echo.
echo   当前版本：
%PY% -V
echo.
echo   建议装 uv（它会自动准备合适的 Python，不用你管版本）：
echo     https://docs.astral.sh/uv/getting-started/installation/
echo.
pause
exit /b 1

:uv_fail
echo   [错误] uv sync 失败，请检查网络后重试。
echo.
echo   也可以绕开 uv，改用 pip 安装：
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
echo     .venv\Scripts\python.exe run.py
echo.
pause
exit /b 1

:deps_fail
echo   [错误] 虚拟环境创建失败，请检查 Python 安装是否完整。
echo.
pause
exit /b 1

:pip_fail
echo   [错误] pip 安装依赖失败，通常是网络问题。
echo.
echo   换成国内镜像再试（阿里云 / 清华，二选一）：
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo   或者改用 uv（自带国内可用的下载源与重试）：
echo     https://docs.astral.sh/uv/getting-started/installation/
echo.
pause
exit /b 1
