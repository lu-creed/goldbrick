import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.bars import router as bars_router
from app.api.indicators import router as indicators_router
from app.api.backtest import router as backtest_router
from app.api.symbols import router as symbols_router
from app.api.tushare import router as tushare_router
from app.api.admin_tushare import router as admin_tushare_router
from app.api.sync import router as sync_router
from app.database import Base, SessionLocal, engine
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


app = FastAPI(title="回测网站 API", version="1.0.0", lifespan=lifespan)

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

app.include_router(symbols_router, prefix="/api")
app.include_router(bars_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(tushare_router, prefix="/api")
app.include_router(admin_tushare_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(indicators_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
