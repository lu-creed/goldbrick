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


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def upsert_symbol_row_for_stock(db: Session, ts_code: str, name: str | None) -> None:
    """保证个股在本地股票池 symbols 表中有对应行（K 线、/api/symbols 下拉、bars 外键都依赖此表）。

    已存在则仅在 name 为空时补名称。
    """
    code = (ts_code or "").strip().upper()
    if not code:
        return
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        db.add(Symbol(ts_code=code, name=name))
    elif sym.name is None and name is not None:
        sym.name = name


def ensure_symbols_for_stock_meta(db: Session) -> int:
    """修复历史数据：仅写了 instrument_meta、未写 symbols 时，按元数据补齐股票池行。

    在 GET /symbols 等入口按需调用，幂等；返回本次新插入条数。
    先用一条反查避免「每次打开 K 线都对全市场逐条 SELECT」。
    """
    missing = (
        db.query(InstrumentMeta.ts_code, InstrumentMeta.name)
        .filter(InstrumentMeta.asset_type == "stock")
        .filter(~InstrumentMeta.ts_code.in_(db.query(Symbol.ts_code)))
        .all()
    )
    if not missing:
        return 0
    for ts_code, name in missing:
        upsert_symbol_row_for_stock(db, ts_code, name)
    db.commit()
    return len(missing)


def ensure_symbols_for_index_meta(db: Session) -> int:
    """与 ensure_symbols_for_stock_meta 对称：数据后台已登记的指数若缺 symbols 行则补齐（幂等）。

    指数 K 线同样挂在 symbols / bars_daily；历史上若只写了 instrument_meta 则需修复。
    """
    missing = (
        db.query(InstrumentMeta.ts_code, InstrumentMeta.name)
        .filter(InstrumentMeta.asset_type == "index")
        .filter(~InstrumentMeta.ts_code.in_(db.query(Symbol.ts_code)))
        .all()
    )
    if not missing:
        return 0
    for ts_code, name in missing:
        upsert_symbol_row_for_stock(db, ts_code, name)
    db.commit()
    return len(missing)


def _parse_trade_date(td_raw) -> date:
    """Tushare trade_date → date。"""
    td_str = str(td_raw)
    if len(td_str) >= 8 and td_str[:8].isdigit():
        return datetime.strptime(td_str[:8], "%Y%m%d").date()
    return datetime.strptime(td_str[:10], "%Y-%m-%d").date()


def _instrument_is_index(db: Session, ts_code: str) -> bool:
    m = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == ts_code.strip().upper()).one_or_none()
    return m is not None and (m.asset_type or "").strip().lower() == "index"


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


def _fetch_and_upsert_index_daily(
    db: Session,
    symbol: Symbol,
    pro,
    code: str,
    s: str,
    e: str,
    w,
) -> tuple[int, bool]:
    """指数日线 index_daily 入库；无复权因子。返回 (行数, adj失败=False)。"""
    w(f"fetch index_daily {code} {s}..{e}")
    df_daily = pro.index_daily(ts_code=code, start_date=s, end_date=e)
    if df_daily is None or df_daily.empty:
        w(f"no index_daily rows for {code}")
        return 0, False

    count = 0
    for _, row in df_daily.iterrows():
        trade_date = _parse_trade_date(row["trade_date"])
        existing = (
            db.query(BarDaily)
            .filter(and_(BarDaily.symbol_id == symbol.id, BarDaily.trade_date == trade_date))
            .one_or_none()
        )
        vol = int(float(row["vol"])) if row.get("vol") is not None else 0
        # Tushare index_daily 的 amount 为千元；复盘/K 线/列表展示均按「元」理解该字段，故此处 ×1000
        amt_k = float(row["amount"]) if row.get("amount") is not None else 0.0
        amt_yuan = amt_k * 1000.0
        payload = dict(
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=vol,
            amount=Decimal(str(amt_yuan)),
            turnover_rate=None,
            source="tushare",
        )
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(BarDaily(symbol_id=symbol.id, trade_date=trade_date, **payload))
        count += 1

    db.commit()
    w(f"upserted bar_rows={count} (index, no adj) for {code}")
    if count > 0:
        try:
            from app.services.indicator_precompute import rebuild_indicator_pre_for_symbol

            npre = rebuild_indicator_pre_for_symbol(db, symbol.id, "none")
            w(f"indicator_pre_daily rebuilt rows={npre} for {code}")
        except Exception as ex:  # noqa: BLE001
            w(f"[indicator_pre] rebuild failed for {code}: {ex}")
    return count, False


def fetch_and_upsert_symbol(
    db: Session,
    symbol: Symbol,
    start: date,
    end: date,
    log_fp=None,
) -> tuple[int, bool]:
    """拉日线并写入 bars_daily。个股用 daily+daily_basic+adj；指数用 index_daily。返回 (行数, 个股复权是否拉取失败)。"""
    token = get_tushare_token()
    if not token:
        msg = "TUSHARE_TOKEN 未配置：请在前端输入并校验 token"
        log.warning(msg)
        if log_fp:
            log_fp.write(msg + "\n")
        return 0, False

    try:
        import tushare as ts
    except ImportError:
        msg = "未安装 tushare：请执行 pip install tushare（建议 Python 3.10+）"
        log.warning(msg)
        if log_fp:
            log_fp.write(msg + "\n")
        return 0, False

    pro = ts.pro_api(token)
    code = symbol.ts_code.strip().upper()
    s, e = _fmt_d(start), _fmt_d(end)

    def w(line: str) -> None:
        log.info(line)
        if log_fp:
            log_fp.write(line + "\n")

    if _instrument_is_index(db, code):
        return _fetch_and_upsert_index_daily(db, symbol, pro, code, s, e, w)

    w(f"fetch daily {code} {s}..{e}")
    df_daily = pro.daily(ts_code=code, start_date=s, end_date=e)
    if df_daily is None or df_daily.empty:
        w(f"no daily rows for {code}")
        return 0, False

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
    if count > 0:
        try:
            from app.services.indicator_precompute import rebuild_indicator_pre_for_symbol

            npre = rebuild_indicator_pre_for_symbol(db, symbol.id, "none")
            w(f"indicator_pre_daily rebuilt rows={npre} for {code}")
        except Exception as ex:  # noqa: BLE001
            w(f"[indicator_pre] rebuild failed for {code}: {ex}")
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

        upsert_symbol_row_for_stock(db, ts_code, _str_or_none(name))

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
    """兼容旧路由名：增量更新上市个股元数据（含 market/exchange）。force 保留参数，行为与每次拉取一致。"""
    _ = force
    return incremental_sync_stock_list_meta(db)


def incremental_sync_stock_list_meta(db: Session) -> dict:
    """从 Tushare stock_basic 增量合并 instrument_meta（仅个股）；不动已有 K 线；指数请用「更新指数列表」入口。"""
    key = "universe_meta_last_sync_date"
    today_str = date.today().isoformat()
    last_sync = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()

    token = get_tushare_token()
    if not token:
        raise HTTPException(status_code=400, detail="TUSHARE_TOKEN 未配置")
    try:
        import tushare as ts
    except ImportError as ex:
        raise HTTPException(status_code=400, detail="未安装 tushare：请先执行 pip install tushare") from ex

    pro = ts.pro_api(token)
    stock_df = pro.stock_basic(
        list_status="L",
        fields="ts_code,name,list_date,market,exchange",
    )
    inserted = 0
    updated = 0
    if stock_df is None or stock_df.empty:
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
            "inserted_stocks": 0,
            "updated_stocks": 0,
            "last_sync_date": date.today(),
        }

    meta_by_code: dict[str, InstrumentMeta] = {
        m.ts_code: m for m in db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "stock").all()
    }

    for _, row in stock_df.iterrows():
        code = str(row.get("ts_code") or "").strip().upper()
        if not code:
            continue
        name = _str_or_none(row.get("name"))
        list_date = _parse_list_date(row.get("list_date"))
        market = _str_or_none(row.get("market"))
        exchange = _str_or_none(row.get("exchange"))

        if code not in meta_by_code:
            m = InstrumentMeta(
                ts_code=code,
                name=name,
                asset_type="stock",
                list_date=list_date,
                market=market,
                exchange=exchange,
            )
            db.add(m)
            meta_by_code[code] = m
            inserted += 1
        else:
            m = meta_by_code[code]
            changed = False
            if m.name != name:
                m.name = name
                changed = True
            if m.list_date != list_date:
                m.list_date = list_date
                changed = True
            if m.market != market:
                m.market = market
                changed = True
            if m.exchange != exchange:
                m.exchange = exchange
                changed = True
            if changed:
                updated += 1

        # 与元数据同步写入股票池：否则 /api/symbols 与 K 线选代码下拉为空（仅 meta 有数据）
        upsert_symbol_row_for_stock(db, code, name)

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
        "inserted_stocks": inserted,
        "updated_stocks": updated,
        "last_sync_date": date.today(),
    }


def fetch_remote_index_basic_rows(market: str | None, limit: int) -> list[dict]:
    """调用 Tushare index_basic，供前端弹窗分页/筛选展示。"""
    token = get_tushare_token()
    if not token:
        raise HTTPException(status_code=400, detail="TUSHARE_TOKEN 未配置")
    try:
        import tushare as ts
    except ImportError as ex:
        raise HTTPException(status_code=400, detail="未安装 tushare：请先执行 pip install tushare") from ex

    pro = ts.pro_api(token)
    lim = min(max(limit, 1), 8000)
    kwargs: dict = {
        "fields": "ts_code,name,market,publisher,list_date",
    }
    if market and str(market).strip():
        kwargs["market"] = str(market).strip()
    df = pro.index_basic(**kwargs)
    if df is None or df.empty:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        if len(out) >= lim:
            break
        code = str(row.get("ts_code") or "").strip().upper()
        if not code:
            continue
        ld_raw = row.get("list_date")
        ld_out: str | None = None
        if ld_raw is not None:
            if isinstance(ld_raw, date):
                ld_out = ld_raw.isoformat()
            else:
                pd = _parse_list_date(str(ld_raw))
                if pd:
                    ld_out = pd.isoformat()
        out.append(
            {
                "ts_code": code,
                "name": _str_or_none(row.get("name")),
                "market": _str_or_none(row.get("market")),
                "publisher": _str_or_none(row.get("publisher")),
                "list_date": ld_out,
            }
        )
    return out


def apply_index_meta_selection(db: Session, items: list[dict]) -> dict:
    """用户勾选的指数写入 instrument_meta + symbols。已存在同代码指数则跳过；与个股代码冲突则报错。"""
    added = 0
    skipped = 0
    for it in items:
        code = str(it.get("ts_code") or "").strip().upper()
        if not code:
            continue
        name = _str_or_none(it.get("name"))
        list_date: date | None = None
        ld = it.get("list_date")
        if ld is not None:
            if isinstance(ld, date):
                list_date = ld
            elif isinstance(ld, str) and len(ld) >= 10:
                try:
                    list_date = datetime.strptime(ld[:10], "%Y-%m-%d").date()
                except ValueError:
                    list_date = _parse_list_date(ld.replace("-", ""))
        existing = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == code).one_or_none()
        if existing:
            if existing.asset_type == "index":
                skipped += 1
                continue
            raise HTTPException(
                status_code=400,
                detail=f"{code} 已作为个股存在于元数据，不能重复登记为指数",
            )
        db.add(
            InstrumentMeta(
                ts_code=code,
                name=name,
                asset_type="index",
                list_date=list_date,
                market=None,
                exchange=None,
            )
        )
        sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
        if not sym:
            db.add(Symbol(ts_code=code, name=name))
        else:
            if name and (not sym.name or not str(sym.name).strip()):
                sym.name = name
        added += 1
    db.commit()
    return {"added": added, "skipped": skipped}


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
                market=None,
                exchange=None,
            )
        )
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": False}
