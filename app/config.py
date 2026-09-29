from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/config.py 往上两级 = enterprise_knowledge_agent/）
BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    """全部配置集中在这里，从 .env 读取，代码里任何地方都用 settings.xxx。"""

    model_config = SettingsConfigDict(
        # 锚定绝对路径：不管从哪个目录启动服务，都能读到项目根的 .env
        # （用相对路径 ".env" 时，它相对当前工作目录，从上级目录启动会静默读不到）
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        # 未声明的键不报错；由 check_unknown_env_keys() 单独告警，见本文件末尾
        extra="ignore",
    )

    # ===== 应用 =====
    APP_NAME: str = "企业知识库智能 Agent"
    APP_ENV: str = "dev"            # dev / test / prod
    DEBUG: bool = True              # 只控制日志详细度与 FastAPI debug 页
    API_PREFIX: str = "/api"

    # 启动参数（统一加 APP_ 前缀，避免和系统环境变量 HOST / PORT 撞名）
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    APP_RELOAD: bool = True

    # 跨域白名单，逗号分隔。留空时：dev 放通全部，非 dev 不放通任何来源
    CORS_ORIGINS: str = ""

    # ===== MySQL =====
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "enterprise_kb"

    # 连接池 / SQL 日志（独立开关，不跟 DEBUG 绑，避免调试页和刷屏绑在一起）
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    SQL_ECHO: bool = False

    # ===== Redis =====
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # ===== Milvus =====
    MILVUS_HOST: str = "127.0.0.1"
    MILVUS_PORT: int = 19530

    # ===== JWT =====
    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # ===== LLM =====
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-chat"

    # ===== Embedding =====
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DIM: int = 1024

    @property
    def mysql_url(self) -> str:
        """SQLAlchemy 连接串。

        user / password / database 必须 URL 编码（quote_plus）：
        密码里只要出现 @ : / # ? 之一，不编码就会把连接串解析错位
        （例如密码 p@ss/w#rd 会让 host 变成 "ss"）。
        """
        return (
            f"mysql+pymysql://{quote_plus(self.MYSQL_USER)}:{quote_plus(self.MYSQL_PASSWORD)}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{quote_plus(self.MYSQL_DATABASE)}"
            f"?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{quote_plus(self.REDIS_PASSWORD)}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins_list(self) -> list[str]:
        """把逗号分隔的 CORS_ORIGINS 拆成列表。"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """单例，全局复用，避免反复读 .env。"""
    return Settings()


def _read_env_keys(env_path: Path) -> list[str]:
    """纯文本解析 .env，取出所有键名（转大写）。

    不依赖 pydantic，方便单独测试。
    """
    if not env_path.exists():
        return []
    keys: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().upper()
        if key:
            keys.append(key)
    return keys


def check_unknown_env_keys() -> list[str]:
    """.env 里写了、但 Settings 未声明的键 —— 基本就是拼写错误。

    返回列表而不抛异常：一行拼错的配置不应该让整个服务起不来，
    但要能在启动日志里被看见（否则会静默回退默认值，很难排查）。
    """
    declared = {name.upper() for name in Settings.model_fields}
    return [key for key in _read_env_keys(ENV_FILE) if key not in declared]


settings = get_settings()
