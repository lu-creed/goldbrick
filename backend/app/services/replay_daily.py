"""单日复盘：在本地已同步 bars 上聚合涨跌分布、涨跌停、换手等（V2.0.1）。"""

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


def _max_bar_date(db: Session) -> Optional[date]:
    r = db.execute(text("SELECT MAX(trade_date) AS mx FROM bars_daily")).scalar()
    return r


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


def build_replay_daily(db: Session, trade_date: date, list_limit: int = 300) -> dict[str, Any]:
    sql = text("""
        SELECT
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
        lim = effective_limit_pct(name, r.market, r.exchange, ts_code, trade_date, list_d)

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
