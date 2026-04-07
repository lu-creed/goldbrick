from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "goldbrick-api"
    database_url: str = "sqlite:///./data/app.db"

    # 数据源（勿提交到仓库）
    tushare_token: str = ""
    tickflow_api_key: str = ""
    tickflow_base_url: str = "https://api.tickflow.org"

    # 日志目录（相对 backend 工作目录）
    log_dir: str = "logs"


def get_backend_root() -> Path:
    """backend/ 目录（含 app 包）。"""
    return Path(__file__).resolve().parent.parent


def resolve_sqlite_url() -> str:
    """将 sqlite 相对路径解析到 backend/data。"""
    s = Settings()
    url = s.database_url
    if url.startswith("sqlite:///./"):
        rel = url.replace("sqlite:///./", "")
        root = get_backend_root()
        path = (root / rel).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"
    return url


settings = Settings()
