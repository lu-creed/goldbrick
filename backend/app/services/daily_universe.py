"""指定交易日的全市场个股行情列表（数据看板 · 个股列表）。

功能：给定某个交易日，列出当天有日线数据的全部 A 股，支持多维度筛选、排序、分页。
涨跌幅等派生字段在内存中计算（需要昨日收盘价），而非在 SQL 层处理，
便于后续在同一批数据上做内存级排序和筛选，全市场约数千条，内存完全可接受。

注意：此模块只读行情数据（bars_daily + instrument_meta + symbols），
不包含同步进度、K 线条数、复权因子情况等「数据池管理」字段，
那些见 /api/sync/data-center 路由（sync.py 中的 data_center 函数）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# 单页最多返回 200 条（防止前端一次拿太多数据卡顿）
_MAX_PAGE_SIZE = 200
# 字符串筛选字段（代码/名称/市场/交易所）的最大长度，超出截断，防止异常长查询
_MAX_STR_FILTER_LEN = 64


@dataclass(frozen=True)
class DailyUniverseFilters:
    """个股列表的所有可选筛选条件，frozen=True 表示构造后不可修改（安全的不变量）。

    未设置（None）的字段不参与过滤——只有明确传入的字段才会缩小结果集。

    字段说明：
    - code_contains: 股票代码子串，如输入 "600" 可筛出所有以 600 开头的股（大小写不敏感）
    - name_contains: 股票名称子串，如输入 "茅台" 可筛出名称含「茅台」的股
    - market_contains/exchange_contains: 市场（如「主板」）或交易所（如「SSE」上交所）子串
    - pct_min/pct_max: 涨跌幅区间（%），如 pct_min=5 可筛出今日涨幅大于 5% 的股
    - open/high/low/close 价格区间
    - volume_min/volume_max: 成交量区间（单位：手）
    - amount_min/amount_max: 成交额区间（单位：元）
    - turnover_min/turnover_max: 换手率区间（%）
    """

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
    """清理字符串筛选值：去首尾空白、空字符串返回 None、超长截断。"""
    if v is None:
        return None
    t = v.strip()
    if not t:
        return None
    return t[:_MAX_STR_FILTER_LEN]


def _norm_range_f(lo: Optional[float], hi: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """对浮点区间做规范化：若用户填反了上下界（lo > hi），自动交换，避免返回空集。"""
    if lo is None and hi is None:
        return None, None
    if lo is not None and hi is not None and lo > hi:
        return hi, lo  # 自动交换，宽容用户填写顺序错误
    return lo, hi


def _norm_range_i(lo: Optional[int], hi: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    """整数区间规范化，逻辑与 _norm_range_f 相同，用于 volume 等整数字段。"""
    if lo is None and hi is None:
        return None, None
    if lo is not None and hi is not None and lo > hi:
        return hi, lo
    return lo, hi


def _item_passes_filters(row: dict[str, Any], f: DailyUniverseFilters) -> bool:
    """判断单条行情数据是否满足全部筛选条件（所有条件 AND 关系）。

    Args:
        row: 由 list_daily_universe 组装的单行 dict，包含 ts_code/name/open/close 等字段。
        f: 筛选条件对象。

    Returns:
        True 表示该行满足所有条件，应保留在结果中。
    """
    # 代码模糊匹配（大小写不敏感：统一转大写后比较）
    if f.code_contains:
        needle = f.code_contains.upper()
        if needle not in str(row.get("ts_code") or "").upper():
            return False
    # 名称模糊匹配（中文名称保持原大小写）
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
        """检查某个浮点字段是否在 [vmin, vmax] 区间内（闭区间）。"""
        v = float(row[key])
        if vmin is not None and v < vmin:
            return False
        if vmax is not None and v > vmax:
            return False
        return True

    # 检查 OHLC 四个价格字段
    if not chk_f("open", f.open_min, f.open_max):
        return False
    if not chk_f("high", f.high_min, f.high_max):
        return False
    if not chk_f("low", f.low_min, f.low_max):
        return False
    if not chk_f("close", f.close_min, f.close_max):
        return False

    # 成交量（整数字段，单独处理）
    vol = int(row["volume"])
    if f.volume_min is not None and vol < f.volume_min:
        return False
    if f.volume_max is not None and vol > f.volume_max:
        return False

    # 成交额
    amt = float(row["amount"])
    if f.amount_min is not None and amt < f.amount_min:
        return False
    if f.amount_max is not None and amt > f.amount_max:
        return False

    # 换手率（可能为 None——指数无换手率）：有筛选要求但字段为 None 时，直接排除该行
    tr = row.get("turnover_rate")
    if f.turnover_min is not None or f.turnover_max is not None:
        if tr is None:
            return False  # 换手率未知，无法判断是否满足，排除
        tf = float(tr)
        if f.turnover_min is not None and tf < f.turnover_min:
            return False
        if f.turnover_max is not None and tf > f.turnover_max:
            return False

    # 涨跌幅（可能为 None——无昨日收盘时无法计算）：有筛选要求但字段为 None 时排除
    pc = row.get("pct_change")
    if f.pct_min is not None or f.pct_max is not None:
        if pc is None:
            return False  # 无昨收，无法计算涨跌幅，排除
        pcf = float(pc)
        if f.pct_min is not None and pcf < f.pct_min:
            return False
        if f.pct_max is not None and pcf > f.pct_max:
            return False

    return True  # 所有条件均通过，保留这一行


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
    """将 HTTP 查询参数整理为内部筛选对象。

    做了以下清洁工作：
    - 字符串：去首尾空白、空字符串→None、超过 64 字符截断
    - 数值区间：如果用户填反（min > max），自动交换，避免返回空集

    Returns:
        DailyUniverseFilters 对象，后续传给 _item_passes_filters 做行级过滤。
    """
    # 各区间规范化（自动纠正上下界颠倒）
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
    """查询 bars_daily 中最新的交易日（用作「未传 trade_date 时的默认值」）。"""
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
    """查询指定交易日全市场个股的 OHLCV + 涨跌幅，支持过滤、排序、分页。

    处理流程：
    1. 确定交易日（未传则取本地最新日）
    2. 一条 SQL 拉出当日所有 A 股日线（含昨收子查询，用于计算涨跌幅）
    3. 在 Python 内存中计算涨跌幅（= (今收 - 昨收) / 昨收 × 100）
    4. 应用筛选条件（内存级，不走数据库）
    5. 按指定字段排序
    6. 切片分页

    Args:
        db: SQLAlchemy 数据库 Session。
        trade_date: 指定交易日；None 时自动取最新日。
        page: 页码（从 1 开始）。
        page_size: 每页条数（上限 _MAX_PAGE_SIZE=200）。
        sort: 排序字段名，如 "pct_change"/"close"/"volume" 等。
        order: "asc"（升序）或 "desc"（降序）。
        filters: 筛选条件，None 时不过滤。

    Returns:
        包含 trade_date/latest_bar_date/total/page/page_size/items 的字典。
        items 是 list[dict]，每个 dict 含 ts_code/name/open/high/low/close/volume/
        amount/turnover_rate/pct_change 字段。
    """
    # 确定实际查询的交易日（用户未指定则取最新日）
    d = trade_date or _max_bar_date(db)
    if d is None:
        # 本地根本没有任何日线，直接返回空结果
        return {
            "trade_date": None,
            "latest_bar_date": None,
            "total": 0,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

    # 参数边界处理
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    sort_keys = {"ts_code", "pct_change", "close", "volume", "amount", "turnover_rate"}
    if sort not in sort_keys:
        sort = "pct_change"  # 非法排序字段，默认按涨跌幅
    reverse = order.lower() != "asc"  # desc → reverse=True，asc → False

    # 核心 SQL：连接 bars_daily + symbols + instrument_meta，获取当日所有 A 股行情
    # 子查询 prev_close：取该股在交易日 d 之前最近一天的收盘价，用于计算涨跌幅
    # LEFT 中没有 prev_close 的行（新股首日/上市第一天）pct_change 将为 None
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

    # 逐行组装：计算涨跌幅（prev_close 为 None 或 0 则 pct_change=None）
    items: list[dict[str, Any]] = []
    for r in rows:
        prev = r.prev_close
        close = float(r.close)
        pct: Optional[float] = None
        if prev is not None and float(prev) > 0:
            # 涨跌幅 = (今收 - 昨收) / 昨收 × 100，保留 3 位小数
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

    # 应用筛选条件（内存过滤，比 SQL 动态拼条件更简单易维护）
    flt = filters or DailyUniverseFilters()
    items = [x for x in items if _item_passes_filters(x, flt)]

    # 排序：None 值的字段需特殊处理，否则 Python 会抛 TypeError（None 不能和数字比较）
    if sort == "ts_code":
        items.sort(key=lambda x: x["ts_code"] or "", reverse=reverse)
    elif sort == "pct_change":
        # None（无昨收）排在末尾，用 (1, 0) 作为 key 让其永远比 (0, 实际值) 大
        def pk(x: dict[str, Any]) -> tuple:
            p = x["pct_change"]
            return (1, 0.0) if p is None else (0, p)

        items.sort(key=pk, reverse=reverse)
    elif sort == "turnover_rate":
        # 同上，None（指数或无换手率）排末尾
        def tk(x: dict[str, Any]) -> tuple:
            p = x["turnover_rate"]
            return (1, 0.0) if p is None else (0, p)

        items.sort(key=tk, reverse=reverse)
    elif sort == "volume":
        items.sort(key=lambda x: x["volume"], reverse=reverse)
    elif sort == "amount":
        items.sort(key=lambda x: x["amount"], reverse=reverse)
    else:
        # 默认 close 排序（兜底分支）
        items.sort(key=lambda x: x["close"], reverse=reverse)

    # 计算分页
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return {
        "trade_date": d,
        "latest_bar_date": _max_bar_date(db),  # 再查一次以获取绝对最新日（可能与 d 不同）
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_items,
    }
