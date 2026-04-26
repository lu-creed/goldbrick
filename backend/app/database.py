"""
数据库引擎与 Session 工厂。

SQLAlchemy 是一个「ORM 框架」：它让我们用 Python 类（models.py 里的 class）来操作数据库，
而不是直接写 SQL。这个文件负责「建立连接」和「创建会话」。

Session（会话）= 一次数据库操作的上下文，类似一个购物车：
  可以往里加很多操作（增删改查），最后统一 commit 提交，或者 rollback 回滚取消。
"""
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import resolve_sqlite_url

log = logging.getLogger(__name__)

# 解析数据库连接地址（SQLite 会转换为绝对路径）
DATABASE_URL = resolve_sqlite_url()

# create_engine：创建数据库连接池
# check_same_thread=False：SQLite 默认只允许创建它的线程使用，设为 False 允许多线程共用（FastAPI 需要）
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

# SessionLocal：Session 工厂，每次调用 SessionLocal() 创建一个新的数据库会话
# autocommit=False：不自动提交，需要手动 db.commit()
# autoflush=False：不自动刷新，减少意外的隐式查询
# expire_on_commit=True：commit 后 ORM 属性过期，下次访问从库重载，
#   避免同步进度的 commit 覆盖了其他连接写入的 pause/cancel 标志（多线程安全）
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=True,
    bind=engine,
)

# Base：所有 ORM 模型（models.py 里的 class）都继承自这个基类
Base = declarative_base()


def ensure_sqlite_instrument_meta_columns() -> None:
    """为已有 SQLite 库追加 instrument_meta 表的新列（market / exchange）。

    背景：SQLite 不支持 ALTER TABLE 自动迁移（不像 PostgreSQL/MySQL）。
    新版本增加了 market/exchange 字段，需要手动用 ALTER TABLE 追加，
    否则老数据库升级后启动会报「no such column」错误。
    """
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(instrument_meta)")).fetchall()
        names = {r[1] for r in rows}
        if "market" not in names:
            conn.execute(text("ALTER TABLE instrument_meta ADD COLUMN market VARCHAR(64)"))
        if "exchange" not in names:
            conn.execute(text("ALTER TABLE instrument_meta ADD COLUMN exchange VARCHAR(16)"))


def ensure_symbols_drop_legacy_enabled_column() -> None:
    """移除 symbols 表中已废弃的 enabled 列（ORM 已删除该字段）。

    早期版本 symbols 表有 enabled 字段，后来业务逻辑调整删除了它。
    但老数据库的表结构里还有这列，INSERT 时会因字段不匹配报错。
    此函数检查并删除该废弃列。
    """
    url = str(engine.url)
    if url.startswith("sqlite"):
        with engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(symbols)")).fetchall()
            col_names = {r[1] for r in rows}
            if "enabled" not in col_names:
                return
            try:
                conn.execute(text("ALTER TABLE symbols DROP COLUMN enabled"))
            except Exception as ex:  # noqa: BLE001
                log.warning("无法 DROP symbols.enabled，请新建库或手动迁移: %s", ex)
        return
    # PostgreSQL 等：列可能仍存在时统一删掉，避免 INSERT 缺列报错
    if "postgresql" in url:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE symbols DROP COLUMN IF EXISTS enabled"))
        except Exception as ex:  # noqa: BLE001
            log.warning("无法 DROP symbols.enabled: %s", ex)


def ensure_user_indicators_definition_json_column() -> None:
    """为 user_indicators 追加 definition_json 列（PRD 指标 DSL 存储字段）。

    当用户从旧版（只有 expr 字段）升级到支持多子线 DSL 的新版时，
    需要在已有表里加一列 definition_json 存放 JSON 公式树。
    """
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(user_indicators)")).fetchall()
        names = {r[1] for r in rows}
        if "definition_json" not in names:
            conn.execute(text("ALTER TABLE user_indicators ADD COLUMN definition_json TEXT"))


def ensure_sync_runs_control_columns() -> None:
    """为 sync_runs 追加协作式控制列（pause_requested / cancel_requested）。

    这两列用于前端向后台工作线程发送「暂停」和「取消」信号。
    工作线程在每只股票开始前轮询这两列来决定是否暂停/停止。
    """
    url = str(engine.url)
    if url.startswith("sqlite"):
        with engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(sync_runs)")).fetchall()
            names = {r[1] for r in rows}
            if "pause_requested" not in names:
                conn.execute(
                    text("ALTER TABLE sync_runs ADD COLUMN pause_requested BOOLEAN DEFAULT 0 NOT NULL")
                )
            if "cancel_requested" not in names:
                conn.execute(
                    text("ALTER TABLE sync_runs ADD COLUMN cancel_requested BOOLEAN DEFAULT 0 NOT NULL")
                )
        return
    if "postgresql" in url:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS pause_requested "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS cancel_requested "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )


def get_db():
    """FastAPI 依赖注入：提供一个数据库 Session，请求结束后自动关闭。

    用法（在路由函数参数里）：
        def my_route(db: Session = Depends(get_db)):
            rows = db.query(SomeModel).all()

    yield 之前：建立连接；yield 之后（finally）：关闭连接，释放数据库资源。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
