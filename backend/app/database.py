"""
数据库引擎与 Session 工厂。

SQLAlchemy 是一个「ORM 框架」：它让我们用 Python 类（models.py 里的 class）来操作数据库，
而不是直接写 SQL。这个文件负责「建立连接」和「创建会话」。

Session（会话）= 一次数据库操作的上下文，类似一个购物车：
  可以往里加很多操作（增删改查），最后统一 commit 提交，或者 rollback 回滚取消。
"""
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import resolve_sqlite_url

log = logging.getLogger(__name__)

# 解析数据库连接地址（SQLite 会转换为绝对路径）
DATABASE_URL = resolve_sqlite_url()

# create_engine：创建数据库连接池
# check_same_thread=False：SQLite 默认只允许创建它的线程使用，设为 False 允许多线程共用（FastAPI 需要）
# timeout=30：SQLite 写锁等待上限 30 秒，避免后台同步线程持锁时前台操作立即报 database is locked
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
)

# WAL 模式（Write-Ahead Logging）：允许读写并发，大幅减少多线程场景下的锁争用。
# 每次新建连接时设置一次（SQLite PRAGMA 是连接级别的）。
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_wal(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

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


# ── 跨方言迁移辅助（0.0.4-dev：为 Postgres 切换预留）─────────────────
def _is_sqlite() -> bool:
    return str(engine.url).startswith("sqlite")


def _is_postgres() -> bool:
    return "postgresql" in str(engine.url)


def _table_columns(conn, table_name: str) -> set[str]:
    """返回 table 当前所有列名的集合；表不存在返回空集。同时支持 SQLite / PostgreSQL。"""
    if _is_sqlite():
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        return {r[1] for r in rows}
    if _is_postgres():
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t"
            ),
            {"t": table_name},
        ).fetchall()
        return {r[0] for r in rows}
    # 其他方言：保守返回空，让外层决定是否 no-op
    return set()


def _table_exists(conn, table_name: str) -> bool:
    return bool(_table_columns(conn, table_name))


def _pg_type(sqlite_type: str) -> str:
    """把 SQLite 方言的 VARCHAR(N)/NUMERIC(p,s)/INTEGER 映射为 PG 对等类型。

    目前我们用到的类型字面量相对窄，直接大小写映射即可。PG 的 NUMERIC 与 SQLite 语义一致、
    VARCHAR(N) 在 PG 也合法，所以多数情况下原样就行。
    """
    return sqlite_type  # 当前使用的类型都是 PG 兼容的子集


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    """跨方言版 ALTER TABLE ADD COLUMN：缺列时才加。

    - SQLite：必须先 PRAGMA 查列再 ADD COLUMN（SQLite 不支持 IF NOT EXISTS）。
    - PostgreSQL：直接用 ADD COLUMN IF NOT EXISTS（PG ≥ 9.6）。
    """
    if _is_postgres():
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {_pg_type(col_type)}")
        )
        return
    # SQLite 或其他：先查再加
    if not _table_exists(conn, table):
        return
    cols = _table_columns(conn, table)
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def ensure_sqlite_instrument_meta_columns() -> None:
    """为已有库追加 instrument_meta 表的新列（market / exchange）。

    历史函数名保留以减少调用点变更；实际内部同时支持 SQLite 与 PostgreSQL。
    """
    if not (_is_sqlite() or _is_postgres()):
        return
    with engine.begin() as conn:
        _add_column_if_missing(conn, "instrument_meta", "market", "VARCHAR(64)")
        _add_column_if_missing(conn, "instrument_meta", "exchange", "VARCHAR(16)")


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
    if not (_is_sqlite() or _is_postgres()):
        return
    with engine.begin() as conn:
        _add_column_if_missing(conn, "user_indicators", "definition_json", "TEXT")


def ensure_sync_runs_control_columns() -> None:
    """为 sync_runs 追加协作式控制列（pause_requested / cancel_requested）。

    这两列用于前端向后台工作线程发送「暂停」和「取消」信号。
    工作线程在每只股票开始前轮询这两列来决定是否暂停/停止。
    """
    if not (_is_sqlite() or _is_postgres()):
        return
    # 这两列是 NOT NULL DEFAULT 0/false，跨方言表达式有差异，单独处理
    with engine.begin() as conn:
        if not _table_exists(conn, "sync_runs"):
            return
        cols = _table_columns(conn, "sync_runs")
        if _is_sqlite():
            if "pause_requested" not in cols:
                conn.execute(
                    text("ALTER TABLE sync_runs ADD COLUMN pause_requested BOOLEAN DEFAULT 0 NOT NULL")
                )
            if "cancel_requested" not in cols:
                conn.execute(
                    text("ALTER TABLE sync_runs ADD COLUMN cancel_requested BOOLEAN DEFAULT 0 NOT NULL")
                )
        else:  # postgres
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


def ensure_screening_history_table() -> None:
    """为老数据库建立 screening_history 表（新版本首次启动时自动创建）。

    SQLite 的 create_all 只会创建「还不存在」的表，不会修改已有表结构，
    所以直接调用 create_all 就够了，这里只是一个语义明确的入口供 lifespan 调用。
    """
    # create_all 已在 lifespan 中统一调用，此函数保留为显式文档说明
    pass


def ensure_backtest_records_table() -> None:
    """为老数据库建立 backtest_records 表（同 ensure_screening_history_table）。"""
    pass


def ensure_backtest_records_columns() -> None:
    """为老 backtest_records 表追加 0.0.4-dev 新增的成本/成交/基准列。

    新列全部 nullable，老记录读出来为 None，前端以 "—" 占位，不影响现有功能。
    """
    if not (_is_sqlite() or _is_postgres()):
        return
    new_cols = [
        ("commission_rate", "NUMERIC(8,6)"),
        ("commission_min", "NUMERIC(10,2)"),
        ("stamp_duty_rate", "NUMERIC(8,6)"),
        ("slippage_bps", "NUMERIC(8,2)"),
        ("lot_size", "INTEGER"),
        ("execution_price", "VARCHAR(16)"),
        ("benchmark_index", "VARCHAR(32)"),
        ("benchmark_return_pct", "NUMERIC(10,4)"),
        ("alpha_pct", "NUMERIC(10,4)"),
    ]
    with engine.begin() as conn:
        if not _table_exists(conn, "backtest_records"):
            return  # 表不存在，create_all 随后会建新版
        for col, col_type in new_cols:
            _add_column_if_missing(conn, "backtest_records", col, col_type)


def ensure_strategy_snapshot_columns() -> None:
    """为老 screening_history / backtest_records 表追加多条件策略快照列。

    新列全部 nullable：
      - screening_history.strategy_snapshot_json
      - backtest_records.buy_strategy_snapshot_json
      - backtest_records.sell_strategy_snapshot_json

    老记录列值为 NULL，API 读取时回退到旧字段（compare_op/threshold 等）展示。
    """
    if not (_is_sqlite() or _is_postgres()):
        return
    with engine.begin() as conn:
        _add_column_if_missing(conn, "screening_history", "strategy_snapshot_json", "TEXT")
        _add_column_if_missing(conn, "backtest_records", "buy_strategy_snapshot_json", "TEXT")
        _add_column_if_missing(conn, "backtest_records", "sell_strategy_snapshot_json", "TEXT")


def ensure_dav_auto_fundamental_columns() -> None:
    """为 dav_stock_watch 追加 auto_payout_ratio / auto_eps 列（AKShare 自动填充字段）。"""
    if not (_is_sqlite() or _is_postgres()):
        return
    with engine.begin() as conn:
        _add_column_if_missing(conn, "dav_stock_watch", "auto_payout_ratio", "NUMERIC(8,4)")
        _add_column_if_missing(conn, "dav_stock_watch", "auto_eps", "NUMERIC(12,4)")


def migrate_for_user_system() -> None:
    """用户体系一次性迁移：删除用户私有表，让 create_all 以新 schema（含 user_id）重建。

    幂等：通过检查 users 表是否已存在判断是否已迁移。
    删除顺序从子表到父表，避免外键约束错误。
    """
    if not (_is_sqlite() or _is_postgres()):
        return
    with engine.begin() as conn:
        if _is_sqlite():
            tables = {
                r[0] for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        else:
            tables = {
                r[0] for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                ).fetchall()
            }
        if "users" in tables:
            return
        for t in [
            "screening_history",
            "backtest_records",
            "watchlist",
            "dav_stock_watch",
            "user_indicators",
        ]:
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        log.info("用户体系迁移：已清除用户私有表，将由 create_all 重建")


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


def ensure_default_admin_user(db) -> None:
    """确保数据库中至少有一个管理员账号。

    首次启动时从 settings.admin_username / settings.admin_password 读取并创建。
    若已存在管理员则跳过（幂等）。
    """
    from app.config import settings
    from app.models import User

    if db.query(User).filter(User.is_admin.is_(True)).first():
        return
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    user = User(
        username=settings.admin_username,
        hashed_password=pwd_context.hash(settings.admin_password),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    log.info("已创建默认管理员账号: %s", settings.admin_username)
