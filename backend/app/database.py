import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import resolve_sqlite_url

log = logging.getLogger(__name__)

DATABASE_URL = resolve_sqlite_url()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
# expire_on_commit=True：commit 后 ORM 属性过期，下次访问从库重载，避免进度 commit 覆盖他连接写入的 pause/cancel 标志。
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=True,
    bind=engine,
)
Base = declarative_base()


def ensure_sqlite_instrument_meta_columns() -> None:
    """SQLite 无自动迁移：为已有库追加 instrument_meta 列。"""
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
    """移除已废弃的 symbols.enabled（ORM 已无该字段）。"""
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
    """SQLite：为 user_indicators 增加 definition_json（PRD 指标 DSL）。"""
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(user_indicators)")).fetchall()
        names = {r[1] for r in rows}
        if "definition_json" not in names:
            conn.execute(text("ALTER TABLE user_indicators ADD COLUMN definition_json TEXT"))


def ensure_sync_runs_control_columns() -> None:
    """为 sync_runs 增加 pause_requested / cancel_requested（协作式暂停、取消）。"""
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
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
