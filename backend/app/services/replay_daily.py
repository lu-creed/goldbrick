"""单日复盘与情绪趋势聚合（口径 0.0.4-dev）：

- 涨跌停口径：走 services/limit_rules.effective_limit_pct，按板块分档（主板 10% / 创业板·科创板 20% / 北交所 30% / ST 5%）。
- 新股识别：除上市首日外，创业板 / 科创板上市后前 5 个交易日无涨跌幅限制，期间不计入涨跌停家数。
- 触及判定：hits_limit_up/hits_limit_down 使用 high/low 相对 prev_close 的容差比较（0.98），与 K 线连板字段保持一致。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.limit_rules import (
    effective_limit_pct,
    hits_limit_down,
    hits_limit_up,
    pct_change,
)

MAJOR_INDEX_CODES: list[tuple[str, str]] = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
]

BUCKET_ORDER: list[tuple[str, str]] = [
    ("limit_down", "跌停"),
    ("lt_neg_8", "-8%以下"),
    ("neg_8_to_5", "-8%~-5%"),
    ("neg_5_to_1", "-5%~-1%"),
    ("neg_1_to_0", "-1%~0%"),
    ("zero", "0%"),
    ("pos_0_to_1", "0%~1%"),
    ("pos_1_to_5", "1%~5%"),
    ("pos_5_to_8", "5%~8%"),
    ("gt_8", "8%以上"),
    ("limit_up", "涨停"),
]

# 新股窗口最多 5 个交易日；取 15 自然日作为「需要精确查询」的上界（覆盖长假）。
_IPO_WINDOW_NATURAL_DAYS = 15


def _max_bar_date(db: Session) -> Optional[date]:
    r = db.execute(text("SELECT MAX(trade_date) AS mx FROM bars_daily")).scalar()
    return _coerce_date(r)


def _coerce_date(v: Any) -> Optional[date]:
    """SQLite 驱动偶尔把 DATE 列读成字符串，这里统一转成 date（无法解析时返回 None）。"""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def _distribution_bucket(p_pct: float) -> str:
    if p_pct < -8:
        return "lt_neg_8"
    if p_pct < -5:
        return "neg_8_to_5"
    if p_pct < -1:
        return "neg_5_to_1"
    if p_pct < 0:
        return "neg_1_to_0"
    if abs(p_pct) < 1e-9:
        return "zero"
    if p_pct <= 1:
        return "pos_0_to_1"
    if p_pct <= 5:
        return "pos_1_to_5"
    if p_pct <= 8:
        return "pos_5_to_8"
    return "gt_8"


def _days_since_ipo_trade(
    db: Session, symbol_id: int, list_date: Optional[date], trade_date: date
) -> Optional[int]:
    """返回 trade_date 是 list_date 之后第几个交易日（list_date 所在日算第 1 个）。

    仅在新股窗口（自然日 ≤ 15）内精确查询；其他情况返回 None 代表「远超豁免期，不必传入」。
    effective_limit_pct 在收到 None 时会退回旧逻辑（仅判上市首日）。
    """
    list_date = _coerce_date(list_date)
    trade_date = _coerce_date(trade_date)
    if list_date is None or trade_date is None or trade_date < list_date:
        return None
    if (trade_date - list_date).days > _IPO_WINDOW_NATURAL_DAYS:
        return None
    row = db.execute(
        text(
            "SELECT COUNT(*) FROM bars_daily "
            "WHERE symbol_id = :sid AND trade_date >= :ld AND trade_date <= :td"
        ),
        {"sid": symbol_id, "ld": list_date, "td": trade_date},
    ).scalar()
    n = int(row or 0)
    return n if n > 0 else None


def build_replay_daily(db: Session, trade_date: date, list_limit: int = 300) -> dict[str, Any]:
    sql = text("""
        SELECT
            s.id AS symbol_id,
            s.ts_code,
            m.name,
            m.market,
            m.exchange,
            m.list_date,
            CAST(b.open AS REAL) AS open,
            CAST(b.high AS REAL) AS high,
            CAST(b.low AS REAL) AS low,
            CAST(b.close AS REAL) AS close,
            CAST(b.turnover_rate AS REAL) AS turnover_rate,
            (
                SELECT CAST(b2.close AS REAL)
                FROM bars_daily b2
                WHERE b2.symbol_id = b.symbol_id AND b2.trade_date < :d
                ORDER BY b2.trade_date DESC
                LIMIT 1
            ) AS prev_close
        FROM bars_daily b
        JOIN symbols s ON s.id = b.symbol_id
        JOIN instrument_meta m ON m.ts_code = s.ts_code
        WHERE b.trade_date = :d
          AND m.asset_type = 'stock'
    """)
    rows = db.execute(sql, {"d": trade_date}).fetchall()

    up = down = flat = 0
    limit_up_n = limit_down_n = 0
    bucket_counts: dict[str, int] = defaultdict(int)
    turn_sum_up = 0.0
    turn_n_up = 0
    turn_sum_down = 0.0
    turn_n_down = 0
    stock_rows: list[dict[str, Any]] = []

    for r in rows:
        ts_code = r.ts_code
        name = r.name
        prev = r.prev_close
        if prev is None or float(prev) <= 0:
            continue
        prev_close = float(prev)
        close = float(r.close)
        high = float(r.high)
        low = float(r.low)
        tr = r.turnover_rate
        trf = float(tr) if tr is not None else None
        list_d = r.list_date

        day_idx = _days_since_ipo_trade(db, r.symbol_id, list_d, trade_date)
        lim = effective_limit_pct(
            name, r.market, r.exchange, ts_code, trade_date, list_d,
            days_since_ipo_trade=day_idx,
        )

        pc = pct_change(close, prev_close)
        if pc is None:
            continue
        p100 = pc * 100.0

        if pc > 0:
            up += 1
            if trf is not None:
                turn_sum_up += trf
                turn_n_up += 1
        elif pc < 0:
            down += 1
            if trf is not None:
                turn_sum_down += trf
                turn_n_down += 1
        else:
            flat += 1

        is_lu = hits_limit_up(high, prev_close, lim)
        is_ld = hits_limit_down(low, prev_close, lim)
        if is_lu:
            limit_up_n += 1
            bkey = "limit_up"
        elif is_ld:
            limit_down_n += 1
            bkey = "limit_down"
        else:
            bkey = _distribution_bucket(p100)
        bucket_counts[bkey] += 1

        stock_rows.append(
            {
                "ts_code": ts_code,
                "name": name,
                "pct_change": round(p100, 3),
                "close": close,
                "turnover_rate": None if trf is None else round(trf, 4),
                "bucket": bkey,
            }
        )

    stock_rows.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
    stock_rows = stock_rows[: max(1, min(list_limit, 2000))]

    buckets_out = [{"key": k, "label": lab, "count": int(bucket_counts.get(k, 0))} for k, lab in BUCKET_ORDER]

    indices_out: list[dict[str, Any]] = []
    for code, title in MAJOR_INDEX_CODES:
        row = db.execute(
            text("""
                SELECT CAST(b.close AS REAL) AS close,
                       CAST(b.amount AS REAL) AS amount,
                       (
                           SELECT CAST(b2.close AS REAL)
                           FROM bars_daily b2
                           JOIN symbols s2 ON s2.id = b2.symbol_id
                           WHERE s2.ts_code = :code AND b2.trade_date < :d
                           ORDER BY b2.trade_date DESC LIMIT 1
                       ) AS prev_close
                FROM bars_daily b
                JOIN symbols s ON s.id = b.symbol_id
                WHERE s.ts_code = :code AND b.trade_date = :d
            """),
            {"code": code, "d": trade_date},
        ).fetchone()
        if not row or row.close is None:
            indices_out.append(
                {
                    "ts_code": code,
                    "name": title,
                    "close": 0.0,
                    "pct_change": None,
                    "amount": 0.0,
                    "data_ok": False,
                    "message": "本地无该指数日线，请在数据后台添加指数并同步",
                }
            )
            continue
        prev_c = row.prev_close
        cls = float(row.close)
        amt = float(row.amount or 0)
        if prev_c is None or float(prev_c) <= 0:
            pct = None
        else:
            pct = round((cls - float(prev_c)) / float(prev_c) * 100.0, 3)
        indices_out.append(
            {
                "ts_code": code,
                "name": title,
                "close": cls,
                "pct_change": pct,
                "amount": amt,
                "data_ok": True,
                "message": None,
            }
        )

    max_d = _max_bar_date(db)

    return {
        "trade_date": trade_date,
        "latest_bar_date": max_d,
        "universe_note": "统计范围：本地 instrument_meta 中 asset_type=stock 且当日存在日线、并有昨收的标的",
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": limit_up_n,
        "limit_down_count": limit_down_n,
        "buckets": buckets_out,
        "turnover_avg_up": None if turn_n_up == 0 else round(turn_sum_up / turn_n_up, 4),
        "turnover_avg_down": None if turn_n_down == 0 else round(turn_sum_down / turn_n_down, 4),
        "indices": indices_out,
        "stocks": stock_rows,
    }


def build_sentiment_trend(db: Session, days: int = 60) -> dict[str, Any]:
    """查询最近 N 个有数据的交易日的情绪趋势，用于情绪仪表盘历史图表。

    逻辑：
    1. 取 bars_daily 中最近 days 个不同交易日（只看 asset_type=stock 的标的）
    2. 对每个交易日重复单日聚合逻辑（涨跌家数、涨跌停数）
    3. 计算综合情绪分：
       base = 50 + (up - down) / (up + down + flat + 1) × 50
       bonus = limit_up / (total + 1) × 20
       sentiment_score = clamp(base + bonus, 0, 100)

    Args:
        days: 最多返回最近多少个交易日的数据（默认 60，上限 120）。
    """
    days = min(max(days, 5), 120)

    # 取最近 days 个有股票日线的交易日
    date_rows = db.execute(
        text("""
            SELECT DISTINCT b.trade_date
            FROM bars_daily b
            JOIN symbols s ON s.id = b.symbol_id
            JOIN instrument_meta m ON m.ts_code = s.ts_code
            WHERE m.asset_type = 'stock'
            ORDER BY b.trade_date DESC
            LIMIT :days
        """),
        {"days": days},
    ).fetchall()

    trading_dates = sorted([r[0] for r in date_rows])

    if not trading_dates:
        return {"days": days, "points": [], "latest_date": None}

    # 批量查询所有日期的涨跌停数据（避免 N 次查询）
    sql = text("""
        SELECT
            b.trade_date,
            s.id AS symbol_id,
            s.ts_code,
            m.name,
            m.market,
            m.exchange,
            m.list_date,
            CAST(b.high AS REAL) AS high,
            CAST(b.low AS REAL) AS low,
            CAST(b.close AS REAL) AS close,
            (
                SELECT CAST(b2.close AS REAL)
                FROM bars_daily b2
                WHERE b2.symbol_id = b.symbol_id
                  AND b2.trade_date < b.trade_date
                ORDER BY b2.trade_date DESC
                LIMIT 1
            ) AS prev_close
        FROM bars_daily b
        JOIN symbols s ON s.id = b.symbol_id
        JOIN instrument_meta m ON m.ts_code = s.ts_code
        WHERE b.trade_date >= :start AND b.trade_date <= :end
          AND m.asset_type = 'stock'
    """)
    start_d = trading_dates[0]
    end_d = trading_dates[-1]
    rows = db.execute(sql, {"start": start_d, "end": end_d}).fetchall()

    day_stats: dict[Any, dict[str, int]] = {
        d: {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0}
        for d in trading_dates
    }

    for r in rows:
        td = r.trade_date
        if td not in day_stats:
            continue
        prev = r.prev_close
        if prev is None or float(prev) <= 0:
            continue
        prev_close = float(prev)
        close = float(r.close)
        high = float(r.high)
        low = float(r.low)

        day_idx = _days_since_ipo_trade(db, r.symbol_id, r.list_date, td)
        lim = effective_limit_pct(
            r.name, r.market, r.exchange, r.ts_code, td, r.list_date,
            days_since_ipo_trade=day_idx,
        )
        pc = pct_change(close, prev_close)
        if pc is None:
            continue

        s = day_stats[td]
        if pc > 0:
            s["up"] += 1
        elif pc < 0:
            s["down"] += 1
        else:
            s["flat"] += 1

        if hits_limit_up(high, prev_close, lim):
            s["limit_up"] += 1
        elif hits_limit_down(low, prev_close, lim):
            s["limit_down"] += 1

    # 组装输出
    points = []
    for d in trading_dates:
        s = day_stats[d]
        up = s["up"]
        down = s["down"]
        flat = s["flat"]
        lu = s["limit_up"]
        ld = s["limit_down"]
        total = up + down + flat

        up_ratio = round(up / total * 100.0, 2) if total > 0 else 0.0
        limit_up_ratio = round(lu / up * 100.0, 2) if up > 0 else 0.0

        # 情绪分：基础分（50 ± 偏移）+ 涨停溢价，clamp 到 [0, 100]
        base = 50.0 + (up - down) / (total + 1) * 50.0
        bonus = lu / (total + 1) * 20.0
        sentiment_score = round(max(0.0, min(100.0, base + bonus)), 2)

        points.append({
            "trade_date": d.isoformat() if hasattr(d, "isoformat") else str(d),
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "limit_up_count": lu,
            "limit_down_count": ld,
            "total": total,
            "up_ratio": up_ratio,
            "limit_up_ratio": limit_up_ratio,
            "sentiment_score": sentiment_score,
        })

    latest = trading_dates[-1]
    return {
        "days": len(points),
        "points": points,
        "latest_date": latest.isoformat() if hasattr(latest, "isoformat") else str(latest),
    }
