"""
后端程序入口：启动时连上数据库、打开定时同步，并把各块业务挂到网址 /api/... 下。

数据怎么流动（记这一条就够）：
浏览器里的页面 → 请求 /api/ 下的某个路径 → 对应 app/api 里某个文件里的函数 →
需要算指标或拉数据时 → 再调用 app/services 里的模块 → 读写数据库。

下面「注册路由」就是把各模块接到 /api 前缀上；具体每个路径干什么，看各 api 文件开头的说明。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.bars import router as bars_router
from app.api.auth import router as auth_router
from app.api.custom_indicators import router as custom_indicators_router
from app.api.indicators import router as indicators_router
from app.api.symbols import router as symbols_router
from app.api.tushare import router as tushare_router
from app.api.admin_tushare import router as admin_tushare_router
from app.api.sync import router as sync_router
from app.api.replay import router as replay_router
from app.api.dashboard import router as dashboard_router
from app.api.screening import router as screening_router
from app.api.backtest import router as backtest_router
from app.api.dav import router as dav_router
from app.api.watchlist import router as watchlist_router
from app.api.auto_update import router as auto_update_router
from app.database import (
    Base,
    SessionLocal,
    engine,
    ensure_sqlite_instrument_meta_columns,
    ensure_symbols_drop_legacy_enabled_column,
    ensure_sync_runs_control_columns,
    ensure_user_indicators_definition_json_column,
    ensure_screening_history_table,
    ensure_backtest_records_table,
    ensure_dav_auto_fundamental_columns,
    migrate_for_user_system,
    ensure_default_admin_user,
)
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services.sync_runner import ensure_default_sync_job
from app.services.indicator_seed import seed_indicators
from app.services.user_indicator_seed import ensure_default_user_indicators
from app.services.derivatives_backfill import maybe_start_backfill_on_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期钩子：FastAPI 启动时执行初始化，关闭时执行清理。

    启动阶段（yield 之前）：
    1. create_all：根据 models.py 里的类定义建表（已存在则跳过）
    2. ensure_* 系列：为老数据库补缺失的列（SQLite 迁移兼容）
    3. ensure_default_sync_job：确保 sync_jobs 表里有一条默认配置记录
    4. seed_indicators：写入内置指标种子（按名称查重，已有则跳过）
    5. ensure_default_user_indicators：写入预置策略模板对应的自定义指标
    6. start_scheduler：启动 APScheduler 定时器（按配置的 cron 自动同步）

    关闭阶段（yield 之后）：
    7. shutdown_scheduler：停止定时器，不等待正在运行的任务
    """
    # 1. 用户体系迁移（首次升级时删除并重建用户私有表）
    migrate_for_user_system()
    # 2. 建表（ORM 根据 models.py 定义自动创建所有表，已存在的表不会重建）
    Base.metadata.create_all(bind=engine)
    # 3. SQLite 迁移兼容：补加新版本引入的列（不会影响已有数据）
    ensure_sqlite_instrument_meta_columns()
    ensure_symbols_drop_legacy_enabled_column()
    ensure_sync_runs_control_columns()
    ensure_user_indicators_definition_json_column()
    ensure_screening_history_table()   # 确保 screening_history 表已创建
    ensure_backtest_records_table()    # 确保 backtest_records 表已创建
    ensure_dav_auto_fundamental_columns()  # 为 dav_stock_watch 追加自动填充字段
    # 4. 确保 sync_jobs 表里至少有一条默认定时任务记录
    db = SessionLocal()
    try:
        ensure_default_sync_job(db)
        # 5. 写入内置指标种子（按名称查重，已存在则跳过；新版本新增的指标自动补入）
        seed_indicators(db)
        # 6. 写入预置自定义指标（策略模板对应的指标，必须在 seed_indicators 之后执行）
        ensure_default_user_indicators(db)
        # 7. 确保至少有一个管理员账号
        ensure_default_admin_user(db)
    finally:
        db.close()
    # 6. 启动定时器（根据数据库里的 cron_expr 配置触发定时同步）
    start_scheduler()
    # 7. 涨跌停连续天数存量回填（仅在版本号落后时触发一次，后台线程执行，不 block 启动）
    maybe_start_backfill_on_startup()
    log.info("app started, scheduler on")
    yield  # 这里应用正常运行，处理请求
    # 5. 关闭阶段：停止定时器
    shutdown_scheduler()
    log.info("app shutdown")


app = FastAPI(title="GoldBrick API", version="0.0.2-dev", lifespan=lifespan)

# CORS（跨域资源共享）：允许前端（本地 5173 端口）向后端发请求
# 本地开发时前端跑在 localhost:5173，后端跑在 localhost:8000，端口不同需要开放 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册各业务模块的路由（按功能分文件，统一挂到 /api 前缀下）
app.include_router(auth_router, prefix="/api")              # 鉴权：登录、注册、用户管理
app.include_router(symbols_router, prefix="/api")          # 股票池：本地关注的代码
app.include_router(bars_router, prefix="/api")             # K 线：按股票+周期取行情
app.include_router(tushare_router, prefix="/api")          # 外部数据源：如全 A 列表供页面勾选
app.include_router(admin_tushare_router, prefix="/api")    # 管理：Tushare token 状态与保存
app.include_router(sync_router, prefix="/api")             # 同步与数据后台、按股票拉历史

# 自定义指标必须先于内置指标注册：否则 GET /indicators/custom 会被 /indicators/{id}
# 当成路径参数 custom 再尝试转 int 失败，返回 422 错误。
app.include_router(custom_indicators_router, prefix="/api")  # 用户自定义指标
app.include_router(indicators_router, prefix="/api")          # 指标库：内置列表与详情
app.include_router(replay_router, prefix="/api")              # 股票复盘：单日聚合
app.include_router(dashboard_router, prefix="/api")           # 数据看板：个股列表（行情维）
app.include_router(screening_router, prefix="/api")           # 条件选股（自定义指标）
app.include_router(backtest_router, prefix="/api")             # 全市场条件选股回测
app.include_router(dav_router, prefix="/api")                  # 大V看板（ABCD分类 + 预期股息率）
app.include_router(watchlist_router, prefix="/api")            # 自选股池（轻量收藏）
app.include_router(auto_update_router, prefix="/api")          # 管理：GitHub 自动更新状态/配置/日志


@app.get("/api/health")
def health():
    """健康检查接口：返回 {"status": "ok"} 表示服务正常运行。"""
    return {"status": "ok"}
