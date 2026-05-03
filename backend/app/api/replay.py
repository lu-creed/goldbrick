"""股票复盘 API（路径前缀 /api/replay/）。

提供单日市场情绪与主要股指走势概览，以及大V视角情绪趋势仪表盘。

核心接口：
  GET /api/replay/daily       - 单日市场情绪复盘
  GET /api/replay/sentiment-trend - 近N日情绪趋势（供历史趋势图使用）

数据来源：本地 bars_daily 日线数据，不实时联网。
业务逻辑均在 app/services/replay_daily.py 中实现。
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user_optional
from app.database import get_db
from app.schemas import (
    ReplayDailyOut,
    ReplayBucket,
    ReplayIndexCard,
    ReplayStockRow,
    SentimentTrendOut,
    SentimentTrendPoint,
)
from app.services.replay_daily import _max_bar_date, build_replay_daily, build_sentiment_trend

router = APIRouter(prefix="/replay", tags=["replay"])


@router.get("/daily", response_model=ReplayDailyOut)
def replay_daily(
    trade_date: Optional[date] = None,
    list_limit: int = Query(300, ge=50, le=2000),
    _user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """单日市场情绪复盘聚合：涨跌家数、分布桶、涨跌停、换手、三大股指、振幅榜。

    Args:
        trade_date: 要复盘的交易日；不传时默认使用本地最新交易日（最近有数据的一天）。
        list_limit: 振幅榜（stocks 字段）返回的最多股票数（默认 300，最多 2000）。

    Returns:
        ReplayDailyOut，包含：
        - up_count/down_count/flat_count: 上涨/下跌/平盘家数
        - limit_up_count/limit_down_count: 涨停/跌停家数
        - buckets: 涨跌幅分布直方图
        - turnover_avg_up/turnover_avg_down: 上涨股平均换手率 vs 下跌股平均换手率
        - indices: 三大股指当日涨跌概况
        - stocks: 振幅最大的股票列表
    """
    d = trade_date
    if d is None:
        d = _max_bar_date(db)
        if d is None:
            raise HTTPException(status_code=400, detail="本地无任何日线数据，请先同步")
    raw = build_replay_daily(db, d, list_limit=list_limit)
    return ReplayDailyOut(
        trade_date=raw["trade_date"],
        latest_bar_date=raw["latest_bar_date"],
        universe_note=raw["universe_note"],
        up_count=raw["up_count"],
        down_count=raw["down_count"],
        flat_count=raw["flat_count"],
        limit_up_count=raw["limit_up_count"],
        limit_down_count=raw["limit_down_count"],
        buckets=[ReplayBucket(**x) for x in raw["buckets"]],
        turnover_avg_up=raw["turnover_avg_up"],
        turnover_avg_down=raw["turnover_avg_down"],
        indices=[ReplayIndexCard(**x) for x in raw["indices"]],
        stocks=[ReplayStockRow(**x) for x in raw["stocks"]],
    )


@router.get("/sentiment-trend", response_model=SentimentTrendOut)
def sentiment_trend(
    days: int = Query(60, ge=5, le=120, description="最多返回最近多少个交易日的情绪数据"),
    _user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """近N日市场情绪趋势（大V视角仪表盘使用）。

    返回每个交易日的涨跌家数、涨跌停数、上涨比例、情绪分等，
    供前端绘制情绪趋势折线图、柱状图等。

    情绪分（sentiment_score）计算方式：
      base  = 50 + (up - down) / (up + down + flat + 1) × 50
      bonus = limit_up / (total + 1) × 20
      score = clamp(base + bonus, 0, 100)
    越高表示市场情绪越乐观，越低表示越悲观。

    Args:
        days: 最近多少个有股票日线的交易日（5~120，默认 60）。
    """
    raw = build_sentiment_trend(db, days=days)
    return SentimentTrendOut(
        days=raw["days"],
        points=[SentimentTrendPoint(**p) for p in raw["points"]],
        latest_date=raw.get("latest_date"),
    )
