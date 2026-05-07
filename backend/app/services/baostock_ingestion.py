"""baostock 数据拉取：作为 Tushare + AKShare 都失败时的最后兜底。

职责单一：只负责从 baostock 拉取个股日线数据，并归一化为与 Tushare 相同的
数据结构（df_daily 列名、turn_map、adj_map），使上层调用代码无需感知数据源。

优点：完全免费，无需 token；数据来源 Wind，质量可靠。
注意：baostock 每次使用需 login/logout；返回的是 ResultData 对象，需转 DataFrame。
"""
import logging
import time

log = logging.getLogger(__name__)

_RETRY_COUNT = 3
_RETRY_DELAY = 2.0


def _ts_code_to_bs_code(ts_code: str) -> str:
    """Tushare ts_code → baostock code：'600000.SH' → 'sh.600000'，'000001.SZ' → 'sz.000001'"""
    code, market = ts_code.split(".")
    return f"{market.lower()}.{code}"


def _bs_fetch_with_retry(func, **kwargs):
    last_ex: Exception | None = None
    for attempt in range(_RETRY_COUNT):
        try:
            return func(**kwargs)
        except Exception as ex:  # noqa: BLE001
            last_ex = ex
            if attempt < _RETRY_COUNT - 1:
                log.warning(
                    "baostock call failed (attempt %d/%d): %s, retrying in %.1fs...",
                    attempt + 1,
                    _RETRY_COUNT,
                    ex,
                    _RETRY_DELAY,
                )
                time.sleep(_RETRY_DELAY)
    raise last_ex  # type: ignore[misc]


def fetch_stock_bars_baostock(
    ts_code: str,
    start_date: str,
    end_date: str,
) -> tuple:
    """通过 baostock 拉取个股日线数据，归一化为与 Tushare 兼容的格式。

    Args:
        ts_code:    Tushare 格式代码，如 '000001.SZ'
        start_date: 8 位日期字符串，如 '20240101'
        end_date:   8 位日期字符串，如 '20240301'

    Returns:
        (df_daily, turn_map, adj_map)
        - df_daily:  DataFrame，列名与 Tushare pro.daily() 一致
                     (trade_date, open, high, low, close, vol, amount)
        - turn_map:  {日期字符串 '20240115': 换手率 float}
        - adj_map:   {日期字符串 '20240115': 复权因子 float}（qfq/raw 反算）
        失败时返回 (None, {}, {})
    """
    try:
        import baostock as bs
        import pandas as pd
    except ImportError:
        log.warning("baostock 未安装，无法使用 baostock fallback（执行 pip install baostock pandas）")
        return None, {}, {}

    bs_code = _ts_code_to_bs_code(ts_code)
    # baostock 日期格式：'YYYY-MM-DD'
    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    fields = "date,open,high,low,close,volume,amount,turn"

    # ---- 登录（suppress baostock 的 print 输出）----
    try:
        import io
        import sys
        _stdout = sys.stdout
        sys.stdout = io.StringIO()
        lg = bs.login()
        sys.stdout = _stdout
        if lg.error_code != "0":
            log.error("[BAOSTOCK] login failed: %s", lg.error_msg)
            return None, {}, {}
    except Exception as ex:  # noqa: BLE001
        log.error("[BAOSTOCK] login exception: %s", ex)
        return None, {}, {}

    try:
        # ---- 拉不复权原始数据 ----
        rs_raw = None
        try:
            rs_raw = _bs_fetch_with_retry(
                bs.query_history_k_data_plus,
                code=bs_code,
                fields=fields,
                start_date=start_fmt,
                end_date=end_fmt,
                frequency="d",
                adjustflag="3",  # 不复权
            )
        except Exception as ex:  # noqa: BLE001
            log.error("[BAOSTOCK] raw daily fetch failed for %s: %s", ts_code, ex)
            return None, {}, {}

        if rs_raw is None or rs_raw.error_code != "0":
            log.error("[BAOSTOCK] query error for %s: %s", ts_code, getattr(rs_raw, "error_msg", "unknown"))
            return None, {}, {}

        rows_raw = []
        while rs_raw.error_code == "0" and rs_raw.next():
            rows_raw.append(rs_raw.get_row_data())

        if not rows_raw:
            return None, {}, {}

        df_raw = pd.DataFrame(rows_raw, columns=rs_raw.fields)
        # 过滤掉 baostock 可能返回的空行
        df_raw = df_raw[df_raw["date"].str.len() == 10].copy()
        if df_raw.empty:
            return None, {}, {}

        # ---- 归一化列名 → Tushare 同名 ----
        df_raw["trade_date"] = df_raw["date"].str.replace("-", "", regex=False)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
        df_daily = df_raw.rename(columns={"volume": "vol"})[
            ["trade_date", "open", "high", "low", "close", "vol", "amount"]
        ]

        # ---- 换手率映射 ----
        turn_map: dict[str, float] = {}
        for _, row in df_raw.iterrows():
            td = str(row["trade_date"])
            tr = row.get("turn")
            if tr not in (None, "", "nan"):
                try:
                    turn_map[td] = float(tr)
                except (TypeError, ValueError):
                    pass

        # ---- 拉前复权数据，反算复权因子 ----
        adj_map: dict[str, float] = {}
        try:
            rs_qfq = _bs_fetch_with_retry(
                bs.query_history_k_data_plus,
                code=bs_code,
                fields="date,close",
                start_date=start_fmt,
                end_date=end_fmt,
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs_qfq and rs_qfq.error_code == "0":
                rows_qfq = []
                while rs_qfq.error_code == "0" and rs_qfq.next():
                    rows_qfq.append(rs_qfq.get_row_data())
                if rows_qfq:
                    df_qfq = pd.DataFrame(rows_qfq, columns=rs_qfq.fields)
                    df_qfq["trade_date"] = df_qfq["date"].str.replace("-", "", regex=False)
                    df_qfq["close"] = pd.to_numeric(df_qfq["close"], errors="coerce")
                    qfq_close = df_qfq.set_index("trade_date")["close"]
                    raw_close = df_raw.set_index("trade_date")["close"]
                    for td in raw_close.index:
                        if td in qfq_close.index:
                            raw_c = float(raw_close[td])
                            qfq_c = float(qfq_close[td])
                            if raw_c != 0 and not (raw_c != raw_c) and not (qfq_c != qfq_c):
                                adj_map[td] = round(qfq_c / raw_c, 6)
        except Exception as ex:  # noqa: BLE001
            log.warning("[BAOSTOCK] qfq fetch failed for %s: %s, adj_factor 将跳过", ts_code, ex)

        return df_daily, turn_map, adj_map

    finally:
        try:
            sys.stdout = io.StringIO()
            bs.logout()
            sys.stdout = _stdout
        except Exception:  # noqa: BLE001
            sys.stdout = _stdout
