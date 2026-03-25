"""Tushare 为主的数据拉取与入库。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdjFactorDaily, AppSetting, BarDaily, InstrumentMeta, Symbol
from app.services.runtime_tokens import get_tushare_token

log = logging.getLogger(__name__)


def _fmt_d(d: date) -> str:
    return d.strftime("%Y%m%d")


def verify_tushare_token_for_sync() -> None:
    """同步任务启动前校验 token 可用性。"""
    token = get_tushare_token()
    if not token:
        raise ValueError("TUSHARE_TOKEN 未配置：请在同步任务页先填写并校验 token")
    try:
        import tushare as ts
    except ImportError as ex:
        raise ValueError("未安装 tushare：请执行 pip install tushare（建议 Python 3.10+）") from ex

    pro = ts.pro_api(token)
    try:
        d = date.today()
        ds = _fmt_d(d)
        pro.daily_basic(
            ts_code="000001.SZ",
            start_date=ds,
            end_date=ds,
            fields="ts_code,trade_date,turnover_rate",
        )
    except Exception as ex:  # noqa: BLE001
        raise ValueError("TUSHARE token 无效或权限不足，请重新设置") from ex


def fetch_and_upsert_symbol(
    db: Session,
    symbol: Symbol,
    start: date,
    end: date,
    log_fp=None,
) -> int:
    """返回写入/更新行数。无 token 时跳过。"""
    token = get_tushare_token()
    if not token:
        msg = "TUSHARE_TOKEN 未配置：请在前端输入并校验 token"
        log.warning(msg)
        if log_fp:
            log_fp.write(msg + "\n")
        return 0

    try:
        import tushare as ts
    except ImportError:
        msg = "未安装 tushare：请执行 pip install tushare（建议 Python 3.10+）"
        log.warning(msg)
        if log_fp:
            log_fp.write(msg + "\n")
        return 0

    pro = ts.pro_api(token)
    code = symbol.ts_code
    s, e = _fmt_d(start), _fmt_d(end)

    def w(line: str) -> None:
        log.info(line)
        if log_fp:
            log_fp.write(line + "\n")

    w(f"fetch daily {code} {s}..{e}")
    df_daily = pro.daily(ts_code=code, start_date=s, end_date=e)
    if df_daily is None or df_daily.empty:
        w(f"no daily rows for {code}")
        return 0

    df_basic = pro.daily_basic(ts_code=code, start_date=s, end_date=e, fields="ts_code,trade_date,turnover_rate")
    adj_fetch_failed = False
    try:
        df_adj = pro.adj_factor(ts_code=code, start_date=s, end_date=e)
    except Exception as ex:  # noqa: BLE001
        w(f"[ADJ_FAIL] adj_factor fetch failed for {code}: {ex}")
        df_adj = None
        adj_fetch_failed = True
    turn_map: dict[str, float] = {}
    if df_basic is not None and not df_basic.empty:
        for _, row in df_basic.iterrows():
            td = str(row["trade_date"])
            if row.get("turnover_rate") is not None:
                try:
                    turn_map[td] = float(row["turnover_rate"])
                except (TypeError, ValueError):
                    pass

    adj_map: dict[str, float] = {}
    if df_adj is not None and not df_adj.empty:
        for _, row in df_adj.iterrows():
            td = str(row.get("trade_date") or "")
            af = row.get("adj_factor")
            if not td or af is None:
                continue
            try:
                adj_map[td] = float(af)
            except (TypeError, ValueError):
                continue

    count = 0
    for _, row in df_daily.iterrows():
        td_raw = row["trade_date"]
        td_str = str(td_raw)
        if len(td_str) == 8:
            trade_date = datetime.strptime(td_str, "%Y%m%d").date()
        else:
            trade_date = datetime.strptime(td_str[:10], "%Y-%m-%d").date()

        existing = (
            db.query(BarDaily)
            .filter(and_(BarDaily.symbol_id == symbol.id, BarDaily.trade_date == trade_date))
            .one_or_none()
        )
        vol = int(float(row["vol"])) if row.get("vol") is not None else 0
        amt = float(row["amount"]) if row.get("amount") is not None else 0.0
        turn = turn_map.get(td_str.replace("-", ""), None)
        if turn is None and td_str in turn_map:
            turn = turn_map[td_str]

        payload = dict(
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=vol,
            amount=Decimal(str(amt)),
            turnover_rate=Decimal(str(turn)) if turn is not None else None,
            source="tushare",
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(
                BarDaily(
                    symbol_id=symbol.id,
                    trade_date=trade_date,
                    **payload,
                )
            )
        count += 1

    db.commit()
    adj_count = 0
    if adj_map:
        for td_str, af in adj_map.items():
            if len(td_str) == 8:
                adj_date = datetime.strptime(td_str, "%Y%m%d").date()
            else:
                adj_date = datetime.strptime(td_str[:10], "%Y-%m-%d").date()
            existing_adj = (
                db.query(AdjFactorDaily)
                .filter(and_(AdjFactorDaily.symbol_id == symbol.id, AdjFactorDaily.trade_date == adj_date))
                .one_or_none()
            )
            if existing_adj:
                existing_adj.adj_factor = Decimal(str(af))
                existing_adj.source = "tushare"
            else:
                db.add(
                    AdjFactorDaily(
                        symbol_id=symbol.id,
                        trade_date=adj_date,
                        adj_factor=Decimal(str(af)),
                        source="tushare",
                    )
                )
            adj_count += 1
        db.commit()
    if adj_fetch_failed:
        w(f"upserted bar_rows={count} adj_factor_rows=0(FAILED) for {code}")
    else:
        w(f"upserted bar_rows={count} adj_factor_rows={adj_count} for {code}")
    return count, adj_fetch_failed


def fetch_all_a_stock_list(db: Session) -> list[dict]:
    """优先返回本地缓存；每日最多尝试一次增量刷新股票列表。"""
    local_rows = db.query(Symbol).order_by(Symbol.ts_code.asc()).all()
    local_out = [{"ts_code": s.ts_code, "name": s.name} for s in local_rows]

    key = "stock_list_last_sync_date"
    last_sync = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    today_str = date.today().isoformat()

    # 已有本地缓存且今天已同步过，直接返回，避免频繁消耗外部接口调用次数。
    if local_out and last_sync and (last_sync.value or "").strip() == today_str:
        return local_out

    token = get_tushare_token()
    if not token:
        if local_out:
            return local_out
        raise HTTPException(status_code=400, detail="TUSHARE_TOKEN 未配置：请在前端输入 token")

    try:
        import tushare as ts
    except ImportError as ex:
        raise HTTPException(
            status_code=400,
            detail="未安装 tushare：请先执行 pip install tushare（建议 Python 3.10+）",
        ) from ex

    pro = ts.pro_api(token)
    try:
        df = pro.stock_basic(list_status="L", fields="ts_code,name")
    except Exception as ex:  # noqa: BLE001
        # 外部接口失败时，优先回退本地缓存，避免页面不可用。
        if local_out:
            return local_out
        raise HTTPException(status_code=400, detail="股票列表拉取失败，请检查 token 权限") from ex
    if df is None or df.empty:
        return local_out

    out: list[dict] = []
    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code") or "").strip().upper()
        if not ts_code:
            continue
        name = row.get("name")

        existing = db.query(Symbol).filter(Symbol.ts_code == ts_code).one_or_none()
        if not existing:
            db.add(Symbol(ts_code=ts_code, name=name, enabled=False))
        else:
            # 保留 enabled 状态；只有 name 为空时才补
            if existing.name is None and name is not None:
                existing.name = name

        out.append({"ts_code": ts_code, "name": name})

    if not last_sync:
        db.add(AppSetting(key=key, value=today_str))
    else:
        last_sync.value = today_str
    db.commit()
    return out


def _parse_list_date(s: str | None) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    if len(s) != 8:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def sync_universe_meta(db: Session, force: bool = False) -> dict:
    """同步个股+主要指数元数据，用于数据后台。"""
    key = "universe_meta_last_sync_date"
    today_str = date.today().isoformat()
    last_sync = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    meta_count = db.query(InstrumentMeta).count()
    if meta_count > 0 and not force:
        stock_count = db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "stock").count()
        index_count = db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "index").count()
        last_sync_date = None
        if last_sync and (last_sync.value or "").strip():
            try:
                last_sync_date = datetime.strptime(last_sync.value.strip(), "%Y-%m-%d").date()
            except ValueError:
                last_sync_date = None
        return {
            "stock_count": int(stock_count),
            "index_count": int(index_count),
            "total": int(stock_count + index_count),
            "from_cache": True,
            "last_sync_date": last_sync_date,
        }

    token = get_tushare_token()
    if not token:
        raise HTTPException(status_code=400, detail="TUSHARE_TOKEN 未配置")
    try:
        import tushare as ts
    except ImportError as ex:
        raise HTTPException(status_code=400, detail="未安装 tushare：请先执行 pip install tushare") from ex

    pro = ts.pro_api(token)

    stock_df = pro.stock_basic(list_status="L", fields="ts_code,name,list_date")
    if stock_df is not None and not stock_df.empty:
        existed_stock_codes = {r[0] for r in db.query(InstrumentMeta.ts_code).filter(InstrumentMeta.asset_type == "stock").all()}
        for _, row in stock_df.iterrows():
            code = str(row.get("ts_code") or "").strip().upper()
            if not code:
                continue
            if code in existed_stock_codes:
                continue
            name = row.get("name")
            list_date = _parse_list_date(row.get("list_date"))
            db.add(InstrumentMeta(ts_code=code, name=name, asset_type="stock", list_date=list_date))

    # 先覆盖 PRD 提到的核心指数
    index_codes = [
        "000001.SH",  # 上证指数
        "399001.SZ",  # 深证成指
        "399006.SZ",  # 创业板指
        "000016.SH",  # 上证50
        "000905.SH",  # 中证500
        "000852.SH",  # 中证1000
        "932000.CSI",  # 中证2000
    ]
    idx_df = pro.index_basic(fields="ts_code,name,list_date")
    idx_map: dict[str, tuple[str | None, date | None]] = {}
    if idx_df is not None and not idx_df.empty:
        for _, row in idx_df.iterrows():
            c = str(row.get("ts_code") or "").strip().upper()
            if not c:
                continue
            idx_map[c] = (row.get("name"), _parse_list_date(row.get("list_date")))
    existed_index_codes = {r[0] for r in db.query(InstrumentMeta.ts_code).filter(InstrumentMeta.asset_type == "index").all()}
    for code in index_codes:
        if code in existed_index_codes:
            continue
        name, list_date = idx_map.get(code, (None, None))
        db.add(InstrumentMeta(ts_code=code, name=name, asset_type="index", list_date=list_date))

    if not last_sync:
        db.add(AppSetting(key=key, value=today_str))
    else:
        last_sync.value = today_str
    db.commit()
    stock_total = db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "stock").count()
    index_total = db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "index").count()
    return {
        "stock_count": int(stock_total),
        "index_count": int(index_total),
        "total": int(stock_total + index_total),
        "from_cache": False,
        "last_sync_date": date.today(),
    }


def bootstrap_meta_from_symbols(db: Session) -> dict:
    """当 instrument_meta 为空时，用 symbols 做本地兜底迁移。"""
    meta_count = db.query(InstrumentMeta).count()
    if meta_count > 0:
        return {"inserted": 0, "skipped": True}

    symbols = db.query(Symbol).all()
    inserted = 0
    for s in symbols:
        code = (s.ts_code or "").strip().upper()
        if not code:
            continue
        exists = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == code).one_or_none()
        if exists:
            continue
        db.add(
            InstrumentMeta(
                ts_code=code,
                name=s.name,
                asset_type="stock",
                list_date=None,
            )
        )
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": False}
