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
from app.api.custom_indicators import router as custom_indicators_router
from app.api.indicators import router as indicators_router
from app.api.symbols import router as symbols_router
from app.api.tushare import router as tushare_router
from app.api.admin_tushare import router as admin_tushare_router
from app.api.sync import router as sync_router
from app.api.replay import router as replay_router
from app.api.dashboard import router as dashboard_router
from app.api.screening import router as screening_router
from app.database import (
    Base,
    SessionLocal,
    engine,
    ensure_sqlite_instrument_meta_columns,
    ensure_symbols_drop_legacy_enabled_column,
    ensure_sync_runs_control_columns,
    ensure_user_indicators_definition_json_column,
)
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services.sync_runner import ensure_default_sync_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_instrument_meta_columns()
    ensure_symbols_drop_legacy_enabled_column()
    ensure_sync_runs_control_columns()
    ensure_user_indicators_definition_json_column()
    db = SessionLocal()
    try:
        ensure_default_sync_job(db)
    finally:
        db.close()
    start_scheduler()
    log.info("app started, scheduler on")
    yield
    shutdown_scheduler()
    log.info("app shutdown")


app = FastAPI(title="GoldBrick API", version="0.0.2-dev", lifespan=lifespan)

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

app.include_router(symbols_router, prefix="/api")  # 股票池：本地关注的代码
app.include_router(bars_router, prefix="/api")  # K 线：按股票+周期取行情
app.include_router(tushare_router, prefix="/api")  # 外部数据源：如全 A 列表供页面勾选
app.include_router(admin_tushare_router, prefix="/api")  # 管理：Tushare token 状态与保存
app.include_router(sync_router, prefix="/api")  # 同步与数据后台、按股票拉历史
# 自定义指标必须先于内置指标注册：否则 GET /indicators/custom 会被 /indicators/{id} 当成路径参数 custom 再转 int 失败，返回 422。
app.include_router(custom_indicators_router, prefix="/api")  # 用户自定义指标
app.include_router(indicators_router, prefix="/api")  # 指标库：内置列表与详情
app.include_router(replay_router, prefix="/api")  # 股票复盘：单日聚合
app.include_router(dashboard_router, prefix="/api")  # 数据看板：个股列表（行情维）
app.include_router(screening_router, prefix="/api")  # 条件选股（自定义指标）

@app.get("/api/health")
def health():
    return {"status": "ok"}
