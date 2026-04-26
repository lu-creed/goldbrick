"""
应用配置：从 .env 文件和环境变量中读取配置项。

使用 pydantic-settings 库，字段名对应环境变量名（大写），如：
  DATABASE_URL=sqlite:///./data/app.db
  TUSHARE_TOKEN=your_token_here
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置类：启动时自动读取 .env 文件和系统环境变量。

    优先级：环境变量 > .env 文件 > 字段默认值。
    extra="ignore"：.env 中多余的字段不会报错，直接忽略。
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "goldbrick-api"
    # 数据库连接串；SQLite 相对路径会被 resolve_sqlite_url() 转为绝对路径
    database_url: str = "sqlite:///./data/app.db"

    # 数据源 token（勿提交到代码仓库，通过 .env 文件配置）
    tushare_token: str = ""          # Tushare 接口 token（需要 ≥320 积分才能调日线接口）
    tickflow_api_key: str = ""       # 备用数据源 key（当前版本暂未使用）
    tickflow_base_url: str = "https://api.tickflow.org"

    # 日志目录（相对 backend 工作目录；程序会在其下创建 sync/ 子目录写同步日志）
    log_dir: str = "logs"


def get_backend_root() -> Path:
    """返回 backend/ 目录的绝对路径（即包含 app 包的上一级目录）。

    用于将相对路径（如日志、数据库）解析为绝对路径，
    确保无论从哪个目录启动服务，路径都正确。
    """
    return Path(__file__).resolve().parent.parent


def resolve_sqlite_url() -> str:
    """将 sqlite:///./data/app.db 中的相对路径解析为绝对路径。

    SQLite 相对路径依赖「当前工作目录」，不同启动方式会指向不同位置。
    此函数将其固定为 backend/data/app.db，并自动创建 data 目录。
    非 SQLite 连接串直接原样返回。
    """
    s = Settings()
    url = s.database_url
    if url.startswith("sqlite:///./"):
        rel = url.replace("sqlite:///./", "")
        root = get_backend_root()
        path = (root / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"
    return url


# 全局配置实例：其他模块通过 from app.config import settings 访问
settings = Settings()
