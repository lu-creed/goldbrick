"""AKShare 基本面数据拉取服务。

职责：
1. 个股分红/EPS 拉取：供 DAV 看板自动填充派息率与 EPS
2. 全市场 PE/PB/市值快照：供个股列表筛选与展示

数据来源稳定性说明：AKShare 依赖第三方网站（东方财富等），偶发接口变更，
调用前建议做版本锁定；单只或单次失败时捕获异常，不中断整体流程。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

_RETRY_COUNT = 3
_RETRY_DELAY = 2.0


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
                    attempt + 1, _RETRY_COUNT, ex, _RETRY_DELAY,
                )
                time.sleep(_RETRY_DELAY)
    raise last_ex  # type: ignore[misc]


def _safe_float(val) -> Optional[float]:
    """将任意值安全转换为 float，'-' / None / 空字符串等非法值返回 None。"""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (f != f) else f  # NaN 检测
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1：个股派息率 / EPS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_payout_ratio_eps(ts_code: str) -> tuple[Optional[float], Optional[float]]:
    """从 AKShare stock_fhps_em 拉取个股分红配送历史，计算近两年平均派息率和最新 EPS。

    派息率计算公式：
        payout_ratio (%) = 派息(每10股, 税前) / 10 / 每股收益 × 100

    Args:
        ts_code: Tushare 格式代码，如 '000001.SZ'

    Returns:
        (avg_payout_ratio_pct, latest_eps)
        - avg_payout_ratio_pct: 近两个有效年份的平均派息率（%），无数据时为 None
        - latest_eps: 最近一期年度 EPS（元/股），无数据时为 None
    """
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare 未安装，无法拉取分红数据")
        return None, None

    symbol = ts_code.split(".")[0]
    try:
        df = _ak_fetch_with_retry(ak.stock_fhps_em, symbol=symbol)
    except Exception as ex:
        log.warning("stock_fhps_em(%s) 失败: %s", ts_code, ex)
        return None, None

    if df is None or df.empty:
        return None, None

    # 字段名可能随 AKShare 版本略有变化，尝试常见名称
    # 典型列：报告期 / 每股收益 / 派息(每10股,税前) / 派息(每10股,税后)
    col_eps = _find_col(df.columns, ["每股收益", "EPS"])
    col_div = _find_col(df.columns, ["派息(每10股,税前)", "派息每10股税前", "派息"])
    col_date = _find_col(df.columns, ["报告期", "公告日期", "除权除息日"])

    if col_eps is None or col_div is None:
        log.warning("stock_fhps_em(%s) 列名未识别, 列: %s", ts_code, list(df.columns))
        return None, None

    # 排序：尽量按报告期降序（最新在前）
    if col_date:
        try:
            df = df.sort_values(col_date, ascending=False).reset_index(drop=True)
        except Exception:
            pass

    payout_ratios: list[float] = []
    latest_eps: Optional[float] = None

    for _, row in df.iterrows():
        eps = _safe_float(row.get(col_eps))
        div10 = _safe_float(row.get(col_div))

        if latest_eps is None and eps is not None and eps > 0:
            latest_eps = round(eps, 4)

        if eps is not None and eps > 0 and div10 is not None and div10 > 0:
            ratio = (div10 / 10.0) / eps * 100.0
            payout_ratios.append(ratio)
            if len(payout_ratios) >= 2:
                break  # 取最近两个有效年份即可

    avg_ratio: Optional[float] = None
    if payout_ratios:
        avg_ratio = round(sum(payout_ratios) / len(payout_ratios), 4)

    return avg_ratio, latest_eps


def _find_col(columns, candidates: list[str]) -> Optional[str]:
    """从 candidates 中找第一个在 columns 里存在的列名。"""
    for c in candidates:
        if c in columns:
            return c
    # 模糊匹配：列名包含候选词
    for c in candidates:
        for col in columns:
            if c in col:
                return col
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2：全市场 PE/PB 日快照
# ─────────────────────────────────────────────────────────────────────────────

def _ak_code_to_ts_code(code: str) -> str:
    """AKShare 6 位纯数字代码 → Tushare ts_code。
    规则：6 开头→.SH；8/4 开头→.BJ；其余（0/3）→.SZ
    """
    s = str(code).strip()
    if s.startswith("6"):
        return f"{s}.SH"
    if s.startswith("8") or s.startswith("4"):
        return f"{s}.BJ"
    return f"{s}.SZ"


def fetch_and_upsert_full_market_fundamental(db, trade_date: date) -> dict:
    """从 AKShare stock_zh_a_spot_em 拉取全市场当日 PE/PB/市值快照并 upsert 到 fundamental_daily。

    Args:
        db: SQLAlchemy Session
        trade_date: 要记录的交易日（通常为 date.today()）

    Returns:
        {"upserted": n, "skipped": n, "error": None 或错误信息}
    """
    try:
        import akshare as ak
    except ImportError:
        return {"upserted": 0, "skipped": 0, "error": "akshare 未安装"}

    try:
        df = _ak_fetch_with_retry(ak.stock_zh_a_spot_em)
    except Exception as ex:
        log.error("stock_zh_a_spot_em 拉取失败: %s", ex)
        return {"upserted": 0, "skipped": 0, "error": str(ex)}

    if df is None or df.empty:
        return {"upserted": 0, "skipped": 0, "error": "返回数据为空"}

    # 字段映射（AKShare 列名）
    col_code = _find_col(df.columns, ["代码", "股票代码"])
    col_pe = _find_col(df.columns, ["市盈率-动态", "市盈率动态", "市盈率"])
    col_pb = _find_col(df.columns, ["市净率"])
    col_total_mv = _find_col(df.columns, ["总市值"])
    col_circ_mv = _find_col(df.columns, ["流通市值"])

    if col_code is None:
        return {"upserted": 0, "skipped": 0, "error": f"找不到代码列, 列名: {list(df.columns)[:10]}"}

    from app.models import FundamentalDaily
    from sqlalchemy import and_

    # 预先查出当日已有记录集合，减少逐行 SELECT
    existing_rows = (
        db.query(FundamentalDaily)
        .filter(FundamentalDaily.trade_date == trade_date)
        .all()
    )
    existing_map: dict[str, FundamentalDaily] = {r.ts_code: r for r in existing_rows}

    upserted = 0
    skipped = 0

    for _, row in df.iterrows():
        raw_code = str(row.get(col_code, "")).strip()
        if not raw_code or len(raw_code) != 6 or not raw_code.isdigit():
            skipped += 1
            continue

        ts_code = _ak_code_to_ts_code(raw_code)
        pe = _safe_float(row.get(col_pe)) if col_pe else None
        pb = _safe_float(row.get(col_pb)) if col_pb else None
        # 市值单位：AKShare 返回元，直接存储
        total_mv = _safe_float(row.get(col_total_mv)) if col_total_mv else None
        circ_mv = _safe_float(row.get(col_circ_mv)) if col_circ_mv else None

        existing = existing_map.get(ts_code)
        if existing:
            existing.pe_ttm = pe
            existing.pb = pb
            existing.total_mv = total_mv
            existing.circ_mv = circ_mv
        else:
            fd = FundamentalDaily(
                ts_code=ts_code,
                trade_date=trade_date,
                pe_ttm=pe,
                pb=pb,
                total_mv=total_mv,
                circ_mv=circ_mv,
                source="akshare",
            )
            db.add(fd)
            existing_map[ts_code] = fd
        upserted += 1

    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        log.error("fundamental_daily commit 失败: %s", ex)
        return {"upserted": 0, "skipped": skipped, "error": str(ex)}

    log.info("fundamental_daily upserted %d rows for %s", upserted, trade_date)
    return {"upserted": upserted, "skipped": skipped, "error": None}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3：个股年度财务指标（按需实时拉取，不入库）
# ─────────────────────────────────────────────────────────────────────────────

def fetch_financial_analysis_indicator(ts_code: str) -> list[dict]:
    """从 AKShare stock_financial_analysis_indicator 拉取个股近 5 年年度财务指标。

    返回格式（列表，按年份升序）：
        [{"period": "2023", "roe": 30.5, "gross_margin": 92.0,
          "debt_ratio": 20.0, "revenue": 1500e8, "net_profit": 700e8}, ...]

    失败或数据为空时返回空列表。
    """
    try:
        import akshare as ak
    except ImportError:
        log.error("akshare 未安装")
        return []

    symbol = ts_code.split(".")[0]
    try:
        df = _ak_fetch_with_retry(
            ak.stock_financial_analysis_indicator,
            symbol=symbol,
            indicator="按年度",
        )
    except Exception as ex:
        log.warning("stock_financial_analysis_indicator(%s) 失败: %s", ts_code, ex)
        return []

    if df is None or df.empty:
        return []

    col_period = _find_col(df.columns, ["报告期", "年份", "period"])
    col_roe = _find_col(df.columns, ["净资产收益率(加权)", "净资产收益率-加权", "净资产收益率"])
    col_gm = _find_col(df.columns, ["销售毛利率"])
    col_dr = _find_col(df.columns, ["资产负债率"])
    col_rev = _find_col(df.columns, ["营业收入"])
    col_np = _find_col(df.columns, ["净利润"])

    if col_period is None:
        log.warning("stock_financial_analysis_indicator(%s) 无报告期列, 列: %s", ts_code, list(df.columns)[:15])
        return []

    try:
        df = df.sort_values(col_period, ascending=False).reset_index(drop=True)
    except Exception:
        pass

    results = []
    for _, row in df.head(5).iterrows():
        period_raw = str(row.get(col_period, "")).strip()
        # 只取年度报告（12-31 结尾 或 4 位年份）
        if len(period_raw) >= 4:
            year = period_raw[:4]
        else:
            continue
        if period_raw and "-" in period_raw and not period_raw.endswith("-12-31"):
            continue  # 跳过季报/半年报

        results.append({
            "period": year,
            "roe": _safe_float(row.get(col_roe) if col_roe else None),
            "gross_margin": _safe_float(row.get(col_gm) if col_gm else None),
            "debt_ratio": _safe_float(row.get(col_dr) if col_dr else None),
            "revenue": _safe_float(row.get(col_rev) if col_rev else None),
            "net_profit": _safe_float(row.get(col_np) if col_np else None),
        })

    return list(reversed(results))  # 升序返回供图表使用


def sync_dav_auto_fundamentals(db, ts_codes: list[str], log_fp=None) -> dict:
    """为指定 ts_code 列表更新 dav_stock_watch 中的 auto_payout_ratio / auto_eps。

    只处理传入列表中实际存在于 dav_stock_watch 的代码；
    单只股票失败不中断整体，失败信息写入 log_fp。
    同一 ts_code 可能属于多个用户，一次性全部更新。

    Returns:
        {"updated": n, "failed": n}
    """
    from app.models import DavStockWatch

    # 去重：一个 ts_code 只拉一次 AKShare，然后批量更新所有用户的同一股
    unique_codes = list(dict.fromkeys(ts_codes))  # 保序去重
    updated = 0
    failed = 0
    for ts_code in unique_codes:
        try:
            pr, eps = fetch_payout_ratio_eps(ts_code)
            rows_affected = (
                db.query(DavStockWatch)
                .filter(DavStockWatch.ts_code == ts_code)
                .update({"auto_payout_ratio": pr, "auto_eps": eps})
            )
            db.commit()
            if rows_affected:
                updated += 1
        except Exception as ex:  # noqa: BLE001
            failed += 1
            msg = f"  WARN DAV auto-fundamental {ts_code}: {ex}\n"
            log.warning(msg.strip())
            if log_fp:
                log_fp.write(msg)
    return {"updated": updated, "failed": failed}
