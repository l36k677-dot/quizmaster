"""集中配置。

所有可变参数一律从环境变量或项目根目录的 .env 读取，
代码中不出现任何明文密钥（验收项：无明文密钥）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 路径常量
BASE_DIR = Path(__file__).resolve().parents[2]      # quizmaster/
APP_DIR = BASE_DIR / "app"
DATA_DIR = BASE_DIR / "data"
SEED_DIR = DATA_DIR / "seed"
CORPUS_DIR = DATA_DIR / "corpus"
BACKUP_DIR = BASE_DIR / "backups"
DB_PATH = DATA_DIR / "quizmaster.db"
STATIC_DIR = APP_DIR / "static"
TEMPLATE_DIR = APP_DIR / "templates"


def load_dotenv(path: Path) -> None:
    """极简 .env 解析器，避免引入额外依赖；已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(BASE_DIR / ".env")


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """运行期配置快照。"""

    app_name: str = "QuizMaster"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    session_secret: str = "insecure-development-secret"
    database_url: str = ""
    question_time_limit_seconds: int = 60
    test_time_limit_seconds: int | None = None
    reward_enabled: bool = True
    rag_enabled: bool = True
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 8
    leaderboard_include_demo: bool = True

    # ------------------------------------------------------------ 派生属性
    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}

    @property
    def is_production(self) -> bool:
        return not self.is_development

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DB_PATH.as_posix()}"

    @property
    def per_question_seconds(self) -> int:
        """每题时限。

        短时限只允许在 development 生效，生产环境永远使用正式时限，
        避免把演示参数带进正式环境。
        """
        if self.is_development and self.test_time_limit_seconds:
            return max(3, int(self.test_time_limit_seconds))
        return max(5, int(self.question_time_limit_seconds))

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    @property
    def rag_available(self) -> bool:
        return self.rag_enabled and self.ai_configured


def _build_settings() -> Settings:
    return Settings(
        app_name=_env_str("APP_NAME", "QuizMaster"),
        app_env=_env_str("APP_ENV", "development"),
        app_host=_env_str("APP_HOST", "127.0.0.1"),
        app_port=int(_env_int("APP_PORT", 8000) or 8000),
        session_secret=_env_str("SESSION_SECRET", "insecure-development-secret"),
        database_url=_env_str("DATABASE_URL", ""),
        question_time_limit_seconds=int(_env_int("QUESTION_TIME_LIMIT_SECONDS", 60) or 60),
        test_time_limit_seconds=_env_int("TEST_TIME_LIMIT_SECONDS", None),
        reward_enabled=_env_bool("REWARD_ENABLED", True),
        rag_enabled=_env_bool("RAG_ENABLED", True),
        ai_base_url=_env_str("AI_BASE_URL", ""),
        ai_api_key=_env_str("AI_API_KEY", ""),
        ai_model=_env_str("AI_MODEL", ""),
        ai_timeout_seconds=int(_env_int("AI_TIMEOUT_SECONDS", 8) or 8),
        leaderboard_include_demo=_env_bool("LEADERBOARD_INCLUDE_DEMO", True),
    )


settings = _build_settings()
