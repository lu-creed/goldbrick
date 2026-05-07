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

    # JWT 鉴权配置
    jwt_secret_key: str = "change-me-in-production"  # 生产环境请通过 .env 覆盖
    jwt_expire_days: int = 7                          # Token 有效天数

    # 初始管理员账号（首次启动时自动创建，若 .env 未配置则使用默认值）
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ── 安全与限流（0.0.4-dev）────────────────────────────────
    # 关闭后所有限流/白名单中间件都不生效（本地开发默认关闭；公测/上线务必打开）
    rate_limit_enabled: bool = False
    # 默认限流（作用于带 @limiter.limit 装饰的路由）；格式为 "次数/时间窗口"，例：10/minute、100/hour
    rate_limit_default: str = "60/minute"
    # 登录限流：防暴力破解，独立更严
    rate_limit_login: str = "5/minute"
    # 全站 IP 白名单：逗号分隔，空字符串表示不启用（允许所有 IP）。命中后仍要过 JWT。
    # 例：IP_WHITELIST=127.0.0.1,10.0.0.0/8,100.64.0.0/10
    ip_whitelist: str = ""
    # 管理员端点 IP 白名单：独立一套，仅保护 /api/sync/*、/api/admin-tushare/*、/api/auto-update/* 等
    # 空则复用 ip_whitelist；两者都空则完全放开
    admin_ip_whitelist: str = ""

    # ── 磁盘/数据保留策略（救火新增）─────────────────────────
    # 历史数据保留年数：超过此年限的日线、复权因子、指标缓存会在定期 cron 里被清理。
    # 0 表示关闭清理（保留全部历史）。默认 10 年，符合绝大多数股票分析场景。
    history_retention_years: int = 10
    # 指标预计算缓存只写入最近 N 天；N=0 表示完全不预算（所有请求回退到内存现算）。
    # 默认 60 天，足够覆盖 MA60 等最长周期指标首屏命中，同时把 indicator_pre_daily 从 GB 级压到 MB 级。
    indicator_pre_recent_days: int = 60
    # 指标预计算启用的复权模式，逗号分隔。默认只预算 qfq；hfq 按需内存现算（减半存储）。
    indicator_pre_adj_modes: str = "qfq"


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
