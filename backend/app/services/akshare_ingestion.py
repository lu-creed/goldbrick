"""AKShare 数据拉取：作为 Tushare 积分不足时的兜底方案。

职责单一：只负责从 AKShare 拉取个股日线数据，并归一化为与 Tushare 相同的
数据结构（df_daily 列名、turn_map、adj_map），使上层调用代码无需感知数据源。

注意：AKShare 依赖第三方网站，稳定性不如 Tushare 官方接口，仅作 fallback。
"""
import logging
import time

log = logging.getLogger(__name__)

_RETRY_COUNT = 3
_RETRY_DELAY = 2.0  # 每次重试前等待秒数


def _ts_code_to_ak_symbol(ts_code: str) -> str:
    """Tushare ts_code → AKShare 6 位代码：'000001.SZ' → '000001'"""
    return ts_code.split(".")[0]


def _ak_fetch_with_retry(func, **kwargs):
    """对 AKShare 调用加重试（网络抖动或限频时自动等待重试）。"""
    last_ex: Exception | None = None
    for attempt in range(_RETRY_COUNT):
        try:
            return func(**kwargs)
        except Exception as ex:  # noqa: BLE001
            last_ex = ex
            if attempt < _RETRY_COUNT - 1:
                log.warning(
                    "AKShare call failed (attempt %d/%d): %s, retrying in %.1fs...",
                    attempt + 1,
                    _RETRY_COUNT,
                    ex,
                    _RETRY_DELAY,
                )
                time.sleep(_RETRY_DELAY)
    raise last_ex  # type: ignore[misc]


def fetch_stock_bars_akshare(
    ts_code: str,
    start_date: str,
    end_date: str,
) -> tuple:
    """通过 AKShare 拉取个股日线数据，归一化为与 Tushare 兼容的格式。

    Args:
        ts_code:    Tushare 格式代码，如 '000001.SZ'
        start_date: 8 位日期字符串，如 '20240101'
        end_date:   8 位日期字符串，如 '20240301'

    Returns:
        (df_daily, turn_map, adj_map)
        - df_daily:  DataFrame，列名与 Tushare pro.daily() 一致
                     (trade_date, open, high, low, close, vol, amount)
        - turn_map:  {日期字符串 '20240115': 换手率 float}（AKShare 已内含，无需单独接口）
        - adj_map:   {日期字符串 '20240115': 复权因子 float}（由 qfq/raw 反算）
        失败时返回 (None, {}, {})
    """
    try:
        import akshare as ak
    except ImportError:
        log.warning("akshare 未安装，无法使用 AKShare fallback（执行 pip install akshare）")
        return None, {}, {}

    symbol = _ts_code_to_ak_symbol(ts_code)

    # ---- 拉原始（不复权）数据 ----
    try:
        df_raw = _ak_fetch_with_retry(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
    except Exception as ex:  # noqa: BLE001
        log.error("[AKSHARE] raw daily fetch failed for %s: %s", ts_code, ex)
        return None, {}, {}

    if df_raw is None or df_raw.empty:
        return None, {}, {}

    # ---- 拉前复权数据，用于反算复权因子 ----
    df_qfq = None
    try:
        df_qfq = _ak_fetch_with_retry(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    except Exception as ex:  # noqa: BLE001
        log.warning("[AKSHARE] qfq fetch failed for %s: %s, adj_factor 将跳过", ts_code, ex)

    # ---- 归一化列名 → Tushare 同名 ----
    df_raw = df_raw.copy()
    df_raw["trade_date"] = df_raw["日期"].astype(str).str.replace("-", "", regex=False)
    df_daily = df_raw.rename(columns={
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "vol",    # 单位：手，与 Tushare 一致
        "成交额": "amount",  # 单位：元，与 Tushare 一致
    })[["trade_date", "open", "high", "low", "close", "vol", "amount"]]

    # ---- 构建换手率映射（AKShare 已内含，无需单独接口）----
    turn_map: dict[str, float] = {}
    for _, row in df_raw.iterrows():
        td = str(row["trade_date"])
        tr = row.get("换手率")
        if tr is not None:
            try:
                turn_map[td] = float(tr)
            except (TypeError, ValueError):
                pass

    # ---- 反算复权因子：adj_factor = close_qfq / close_raw ----
    adj_map: dict[str, float] = {}
    if df_qfq is not None and not df_qfq.empty:
        df_qfq = df_qfq.copy()
        df_qfq["trade_date"] = df_qfq["日期"].astype(str).str.replace("-", "", regex=False)
        qfq_close = df_qfq.set_index("trade_date")["收盘"]
        raw_close = df_raw.set_index("trade_date")["close"]
        for td in raw_close.index:
            if td in qfq_close.index:
                raw_c = float(raw_close[td])
                qfq_c = float(qfq_close[td])
                if raw_c != 0:
                    adj_map[td] = round(qfq_c / raw_c, 6)

    return df_daily, turn_map, adj_map
