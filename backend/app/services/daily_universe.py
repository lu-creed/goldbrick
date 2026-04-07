"""指定交易日的全市场个股行情列表（数据看板 · 个股列表）。

只读 bars_daily + 元数据表，不包含同步进度、条数、复权因子同步状态等「数据池」字段。
筛选在内存中进行（与排序、分页同在一批数据上），便于支持涨跌幅等派生字段；全市场约数千条量级可接受。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_PAGE_SIZE = 200
_MAX_STR_FILTER_LEN = 64


@dataclass(frozen=True)
class DailyUniverseFilters:
    """个股列表可选筛选；未设置的项不参与过滤。"""

    code_contains: Optional[str] = None
    name_contains: Optional[str] = None
    market_contains: Optional[str] = None
    exchange_contains: Optional[str] = None
    pct_min: Optional[float] = None
    pct_max: Optional[float] = None
    open_min: Optional[float] = None
    open_max: Optional[float] = None
    high_min: Optional[float] = None
    high_max: Optional[float] = None
    low_min: Optional[float] = None
    low_max: Optional[float] = None
    close_min: Optional[float] = None
    close_max: Optional[float] = None
    volume_min: Optional[int] = None
    volume_max: Optional[int] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    turnover_min: Optional[float] = None
    turnover_max: Optional[float] = None


def _norm_str(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    t = v.strip()
    if not t:
        return None
    return t[:_MAX_STR_FILTER_LEN]


def _norm_range_f(lo: Optional[float], hi: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """闭合区间；若用户填反了上下界则交换。"""
    if lo is None and hi is None:
        return None, None
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


def _norm_range_i(lo: Optional[int], hi: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if lo is None and hi is None:
        return None, None
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


def _item_passes_filters(row: dict[str, Any], f: DailyUniverseFilters) -> bool:
    """对单行 dict（与 list_daily_universe 组装的结构一致）做筛选。"""
    if f.code_contains:
        needle = f.code_contains.upper()
        if needle not in str(row.get("ts_code") or "").upper():
            return False
    if f.name_contains:
        name = row.get("name") or ""
        if f.name_contains not in name:
            return False
    if f.market_contains:
        m = row.get("market") or ""
        if f.market_contains not in m:
            return False
    if f.exchange_contains:
        ex = row.get("exchange") or ""
        if f.exchange_contains not in ex:
            return False

    def chk_f(key: str, vmin: Optional[float], vmax: Optional[float]) -> bool:
        v = float(row[key])
        if vmin is not None and v < vmin:
            return False
        if vmax is not None and v > vmax:
            return False
        return True

    if not chk_f("open", f.open_min, f.open_max):
        return False
    if not chk_f("high", f.high_min, f.high_max):
        return False
    if not chk_f("low", f.low_min, f.low_max):
        return False
    if not chk_f("close", f.close_min, f.close_max):
        return False

    vol = int(row["volume"])
    if f.volume_min is not None and vol < f.volume_min:
        return False
    if f.volume_max is not None and vol > f.volume_max:
        return False

    amt = float(row["amount"])
    if f.amount_min is not None and amt < f.amount_min:
        return False
    if f.amount_max is not None and amt > f.amount_max:
        return False

    tr = row.get("turnover_rate")
    if f.turnover_min is not None or f.turnover_max is not None:
        if tr is None:
            return False
        tf = float(tr)
        if f.turnover_min is not None and tf < f.turnover_min:
            return False
        if f.turnover_max is not None and tf > f.turnover_max:
            return False

    pc = row.get("pct_change")
    if f.pct_min is not None or f.pct_max is not None:
        if pc is None:
            return False
        pcf = float(pc)
        if f.pct_min is not None and pcf < f.pct_min:
            return False
        if f.pct_max is not None and pcf > f.pct_max:
            return False

    return True


def parse_daily_universe_filters(
    *,
    code_contains: Optional[str] = None,
    name_contains: Optional[str] = None,
    market_contains: Optional[str] = None,
    exchange_contains: Optional[str] = None,
    pct_min: Optional[float] = None,
    pct_max: Optional[float] = None,
    open_min: Optional[float] = None,
    open_max: Optional[float] = None,
    high_min: Optional[float] = None,
    high_max: Optional[float] = None,
    low_min: Optional[float] = None,
    low_max: Optional[float] = None,
    close_min: Optional[float] = None,
    close_max: Optional[float] = None,
    volume_min: Optional[int] = None,
    volume_max: Optional[int] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    turnover_min: Optional[float] = None,
    turnover_max: Optional[float] = None,
) -> DailyUniverseFilters:
    """将 HTTP 查询参数整理为筛选对象（去空白、截断字符串、修正反了的区间）。"""
    pct_min, pct_max = _norm_range_f(pct_min, pct_max)
    open_min, open_max = _norm_range_f(open_min, open_max)
    high_min, high_max = _norm_range_f(high_min, high_max)
    low_min, low_max = _norm_range_f(low_min, low_max)
    close_min, close_max = _norm_range_f(close_min, close_max)
    volume_min, volume_max = _norm_range_i(volume_min, volume_max)
    amount_min, amount_max = _norm_range_f(amount_min, amount_max)
    turnover_min, turnover_max = _norm_range_f(turnover_min, turnover_max)
    return DailyUniverseFilters(
        code_contains=_norm_str(code_contains),
        name_contains=_norm_str(name_contains),
        market_contains=_norm_str(market_contains),
        exchange_contains=_norm_str(exchange_contains),
        pct_min=pct_min,
        pct_max=pct_max,
        open_min=open_min,
        open_max=open_max,
        high_min=high_min,
        high_max=high_max,
        low_min=low_min,
        low_max=low_max,
        close_min=close_min,
        close_max=close_max,
        volume_min=volume_min,
        volume_max=volume_max,
        amount_min=amount_min,
        amount_max=amount_max,
        turnover_min=turnover_min,
        turnover_max=turnover_max,
    )


def _max_bar_date(db: Session) -> Optional[date]:
    r = db.execute(text("SELECT MAX(trade_date) AS mx FROM bars_daily")).scalar()
    return r


def list_daily_universe(
    db: Session,
    trade_date: Optional[date],
    page: int,
    page_size: int,
    sort: str,
    order: str,
    filters: Optional[DailyUniverseFilters] = None,
) -> dict[str, Any]:
    d = trade_date or _max_bar_date(db)
    if d is None:
        return {
            "trade_date": None,
            "latest_bar_date": None,
            "total": 0,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    sort_keys = {"ts_code", "pct_change", "close", "volume", "amount", "turnover_rate"}
    if sort not in sort_keys:
        sort = "pct_change"
    reverse = order.lower() != "asc"

    sql = text("""
        SELECT
            s.ts_code,
            m.name,
            m.market,
            m.exchange,
            CAST(b.open AS REAL) AS open,
            CAST(b.high AS REAL) AS high,
            CAST(b.low AS REAL) AS low,
            CAST(b.close AS REAL) AS close,
            b.volume,
            CAST(b.amount AS REAL) AS amount,
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
    rows = db.execute(sql, {"d": d}).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        prev = r.prev_close
        close = float(r.close)
        pct: Optional[float] = None
        if prev is not None and float(prev) > 0:
            pct = round((close - float(prev)) / float(prev) * 100.0, 3)

        tr = r.turnover_rate
        items.append(
            {
                "ts_code": r.ts_code,
                "name": r.name,
                "market": r.market,
                "exchange": r.exchange,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": close,
                "volume": int(r.volume),
                "amount": float(r.amount),
                "turnover_rate": None if tr is None else float(tr),
                "pct_change": pct,
            }
        )

    flt = filters or DailyUniverseFilters()
    items = [x for x in items if _item_passes_filters(x, flt)]

    if sort == "ts_code":
        items.sort(key=lambda x: x["ts_code"] or "", reverse=reverse)
    elif sort == "pct_change":

        def pk(x: dict[str, Any]) -> tuple:
            p = x["pct_change"]
            return (1, 0.0) if p is None else (0, p)

        items.sort(key=pk, reverse=reverse)
    elif sort == "turnover_rate":

        def tk(x: dict[str, Any]) -> tuple:
            p = x["turnover_rate"]
            return (1, 0.0) if p is None else (0, p)

        items.sort(key=tk, reverse=reverse)
    elif sort == "volume":
        items.sort(key=lambda x: x["volume"], reverse=reverse)
    elif sort == "amount":
        items.sort(key=lambda x: x["amount"], reverse=reverse)
    else:
        items.sort(key=lambda x: x["close"], reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {
        "trade_date": d,
        "latest_bar_date": _max_bar_date(db),
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_items,
    }
