"""Tushare 数据拉取与入库：个股/指数日线、复权因子、股票列表、元数据的增量同步。

主要功能：
  - fetch_and_upsert_symbol: 拉取单只股票（或指数）的日线和复权因子，写入 bars_daily / adj_factors_daily
  - fetch_all_a_stock_list: 获取全量 A 股代码列表（优先本地缓存，每日最多刷新一次）
  - incremental_sync_stock_list_meta: 增量合并 instrument_meta（含市场、交易所字段）
  - apply_index_meta_selection: 将用户勾选的指数写入元数据与 symbols 池
  - bootstrap_meta_from_symbols: 历史兼容：若 instrument_meta 为空，从 symbols 表迁移数据

Tushare 是国内常用的股票数据接口（https://tushare.pro）。
使用前需在前端配置有效的 Tushare token（需要 ≥320 积分，才能调用 daily 和 adj_factor 接口）。
"""
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

_TUSHARE_QUOTA_KEYWORDS = (
    # 中文：Tushare 的业务错误消息常见片段
    "权限", "积分", "每分钟最多", "每小时最多", "每天最多", "超出限制", "没有调用",
    # 英文：部分 SDK/中间层抛出的英文
    "quota", "limit exceeded", "permission", "rate limit",
)


def _is_tushare_quota_error(ex: Exception) -> bool:
    """判断异常是否为 Tushare 积分/权限/频率限制错误（而非网络中断或数据问题）。

    命中任一关键词即视作"额度错误"，后续会自动切换到 AKShare fallback；
    不命中的错误（如 ConnectionError、超时）会直接上抛，由重试/日志处理。
    """
    msg = str(ex).lower()
    return any(k.lower() in msg for k in _TUSHARE_QUOTA_KEYWORDS)


def _fmt_d(d: date) -> str:
    """把 date 对象转换为 Tushare 所需的 8 位字符串格式，如 2024-01-15 → '20240115'。"""
    return d.strftime("%Y%m%d")


def _str_or_none(v) -> str | None:
    """把任意值转换为 stripped 字符串，空字符串和 None 都返回 None（避免存入空白字符串）。"""
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def upsert_symbol_row_for_stock(db: Session, ts_code: str, name: str | None) -> None:
    """确保指定股票代码在 symbols 表中有一条记录（K 线、下拉选单、bars 外键都依赖此表）。

    - 若不存在：新建一行（ts_code + name）
    - 若已存在且 name 为空：补充名称（修复历史数据遗漏）
    - 若已存在且 name 非空：不修改（避免覆盖更精确的名称）

    幂等操作：多次调用同一 ts_code 不会重复插入。
    """
    code = (ts_code or "").strip().upper()
    if not code:
        return
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        db.add(Symbol(ts_code=code, name=name))
    elif sym.name is None and name is not None:
        sym.name = name  # 仅在名称缺失时补全


def ensure_symbols_for_stock_meta(db: Session) -> int:
    """修复历史数据：若 instrument_meta 有记录但 symbols 无对应行，按元数据补齐 symbols。

    背景：早期版本可能先写了 instrument_meta，没有同步写 symbols，
    导致 /api/symbols 下拉为空，K 线无法选股。此函数幂等修复。

    先用一条反查（NOT IN 子查询）找出缺失行，避免对全市场逐条 SELECT N+1。

    Returns:
        本次新插入的 symbols 行数。
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
    """与 ensure_symbols_for_stock_meta 对称：修复指数的 symbols 行缺失问题（幂等）。

    指数 K 线同样挂在 symbols / bars_daily 表；若只写了 instrument_meta 则需此函数修复。

    Returns:
        本次新插入的 symbols 行数。
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
    """将 Tushare 返回的 trade_date 字段解析为 Python date 对象。

    Tushare 的 trade_date 通常是 '20240115'（8 位字符串），
    但部分接口可能返回 '2024-01-15'（ISO 格式）或其他类型，此处统一兼容。
    """
    td_str = str(td_raw)
    if len(td_str) >= 8 and td_str[:8].isdigit():
        return datetime.strptime(td_str[:8], "%Y%m%d").date()
    return datetime.strptime(td_str[:10], "%Y-%m-%d").date()


def _instrument_is_index(db: Session, ts_code: str) -> bool:
    """查询数据库，判断该代码是否为指数（instrument_meta.asset_type == 'index'）。

    指数和个股使用不同的 Tushare 接口（index_daily vs daily），调用前需先区分。
    """
    m = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == ts_code.strip().upper()).one_or_none()
    return m is not None and (m.asset_type or "").strip().lower() == "index"


def verify_tushare_token_for_sync() -> None:
    """同步任务启动前校验 Tushare token 可用性。

    做两件事：
    1. 检查 token 是否已配置（非空）
    2. 实际调用 daily_basic 接口验证 token 有效且权限足够

    Raises:
        ValueError: token 未配置、tushare 未安装、token 无效/权限不足时抛出。
    """
    token = get_tushare_token()
    if not token:
        raise ValueError("TUSHARE_TOKEN 未配置：请在同步任务页先填写并校验 token")
    try:
        import tushare as ts
    except ImportError as ex:
        raise ValueError("未安装 tushare：请执行 pip install tushare（建议 Python 3.10+）") from ex

    pro = ts.pro_api(token)
    try:
        # 用一个轻量接口（daily_basic）验证，比 daily 接口省积分
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
    """拉取指数日线（Tushare index_daily 接口）并写入 bars_daily。

    与个股日线的区别：
    - 接口名不同：index_daily（指数）vs daily（个股）
    - 指数无复权因子（不分红送股），adj_factor 固定为 1.0，不需拉取
    - amount 字段单位：Tushare index_daily 的 amount 为「千元」，写库时 ×1000 转换为元
      （统一与个股的成交额单位，前端展示时一致）
    - turnover_rate 留 None（指数不计算换手率）

    Args:
        symbol: Symbol ORM 对象（含 id 和 ts_code）。
        pro: 已初始化的 Tushare pro API 对象。
        code: 证券代码字符串（如 '000001.SH'）。
        s/e: 起止日期（格式 '20240101'）。
        w: 日志写入函数（同时写文件和标准日志）。

    Returns:
        (写入行数, False)：第二个值固定 False（指数不拉复权因子，所以不存在「adj 失败」）。
    """
    w(f"fetch index_daily {code} {s}..{e}")
    df_daily = pro.index_daily(ts_code=code, start_date=s, end_date=e)
    if df_daily is None or df_daily.empty:
        w(f"no index_daily rows for {code}")
        return 0, False

    # 批量预加载当前日期范围内已有的 bars，避免对每行单独 SELECT（N+1 → 1 次读）
    start_dt = datetime.strptime(s, "%Y%m%d").date()
    end_dt   = datetime.strptime(e, "%Y%m%d").date()
    existing_bars: dict = {
        r.trade_date: r
        for r in db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == symbol.id,
            BarDaily.trade_date >= start_dt,
            BarDaily.trade_date <= end_dt,
        )
        .all()
    }

    count = 0
    for _, row in df_daily.iterrows():
        trade_date = _parse_trade_date(row["trade_date"])
        existing = existing_bars.get(trade_date)
        vol = int(float(row["vol"])) if row.get("vol") is not None else 0
        # Tushare index_daily 的 amount 为千元，统一转换为元（与个股 daily 接口对齐）
        amt_k = float(row["amount"]) if row.get("amount") is not None else 0.0
        amt_yuan = amt_k * 1000.0
        payload = dict(
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=vol,
            amount=Decimal(str(amt_yuan)),
            turnover_rate=None,  # 指数无换手率
            source="tushare",
        )
        if existing:
            # 已有记录：覆盖更新（避免重复插入引发主键冲突）
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(BarDaily(symbol_id=symbol.id, trade_date=trade_date, **payload))
        count += 1

    db.commit()
    w(f"upserted bar_rows={count} (index, no adj) for {code}")
    # 注意：本函数不负责触发 indicator_pre_daily 重建。
    # sync_runner 在调用 fetch_and_upsert_symbol 之后会对 qfq + hfq 两个口径各
    # rebuild 一次（见 sync_runner.py:375-381）。历史上这里曾有一段用 "none"
    # 模式调 rebuild 的代码 —— 实际上是 no-op（rebuild 只认 qfq/hfq），纯属死
    # 代码，已于 wip/fix-ingestion-dead-rebuild 删除。
    return count, False


def fetch_and_upsert_symbol(
    db: Session,
    symbol: Symbol,
    start: date,
    end: date,
    log_fp=None,
) -> tuple[int, bool]:
    """拉取单只股票（或指数）的日线及复权因子，并以 upsert 方式写入数据库。

    个股处理流程（指数走 _fetch_and_upsert_index_daily 分支）：
    1. 调用 Tushare daily 接口拉取日线（OHLCV + 成交额）
    2. 调用 daily_basic 接口拉取换手率（单独接口，避免频繁调用主日线接口）
    3. 调用 adj_factor 接口拉取复权因子（失败时记 adj_fetch_failed=True，不中断）
    4. 将三份数据合并，逐行 upsert 到 bars_daily（已有则更新，没有则新建）
    5. 将复权因子单独写入 adj_factors_daily
    （indicator_pre_daily 重建不在本函数职责内：由上层 sync_runner 对 qfq/hfq
     两口径分别触发，见 sync_runner.py:375-381）

    Args:
        db: 数据库 Session。
        symbol: 要拉取的股票 ORM 对象（包含 id 和 ts_code）。
        start: 拉取起始日期（含）。
        end: 拉取结束日期（含）。
        log_fp: 可选的日志文件对象（写同步日志文件用）；None 时只写标准日志。

    Returns:
        (写入/更新的 bar 行数, adj_fetch_failed)
        adj_fetch_failed=True 表示本次复权因子拉取失败（K 线仍正常写入）。
    """
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
        """同时写标准日志和同步日志文件（sync log file）。"""
        log.info(line)
        if log_fp:
            log_fp.write(line + "\n")

    # 指数走单独分支（不同接口，无复权因子）
    if _instrument_is_index(db, code):
        return _fetch_and_upsert_index_daily(db, symbol, pro, code, s, e, w)

    # ---- 个股：拉三份数据（Tushare 优先，积分/权限不足时自动切换 AKShare）----
    w(f"fetch daily {code} {s}..{e}")
    df_daily = None
    turn_map: dict[str, float] = {}
    adj_map: dict[str, float] = {}
    adj_fetch_failed = False
    data_source = "tushare"

    try:
        df_daily = pro.daily(ts_code=code, start_date=s, end_date=e)
    except Exception as ex:  # noqa: BLE001
        if _is_tushare_quota_error(ex):
            w(f"[TUSHARE_QUOTA] {code}: {ex}")
        else:
            raise

    if df_daily is None or df_daily.empty:
        # Tushare 无数据（配额报错被吞 or 返回空）→ 尝试 AKShare
        from app.services.akshare_ingestion import fetch_stock_bars_akshare  # noqa: PLC0415
        w(f"[AKSHARE_FALLBACK] trying AKShare for {code}")
        df_daily, turn_map, adj_map = fetch_stock_bars_akshare(code, s, e)
        data_source = "akshare"
        if not adj_map:
            adj_fetch_failed = True
        if df_daily is None or df_daily.empty:
            # AKShare 也失败 → 尝试 baostock（免费，无需 token，最后兜底）
            from app.services.baostock_ingestion import fetch_stock_bars_baostock  # noqa: PLC0415
            w(f"[BAOSTOCK_FALLBACK] trying baostock for {code}")
            df_daily, turn_map, adj_map = fetch_stock_bars_baostock(code, s, e)
            data_source = "baostock"
            if not adj_map:
                adj_fetch_failed = True
            if df_daily is None or df_daily.empty:
                w(f"no daily rows for {code} (Tushare, AKShare, baostock all empty)")
                return 0, False
    else:
        # Tushare 成功：补拉换手率和复权因子
        df_basic = pro.daily_basic(ts_code=code, start_date=s, end_date=e, fields="ts_code,trade_date,turnover_rate")
        try:
            df_adj = pro.adj_factor(ts_code=code, start_date=s, end_date=e)
        except Exception as ex:  # noqa: BLE001
            w(f"[ADJ_FAIL] adj_factor fetch failed for {code}: {ex}")
            df_adj = None
            adj_fetch_failed = True

        if df_basic is not None and not df_basic.empty:
            for _, row in df_basic.iterrows():
                td = str(row["trade_date"])
                if row.get("turnover_rate") is not None:
                    try:
                        turn_map[td] = float(row["turnover_rate"])
                    except (TypeError, ValueError):
                        pass

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

    # ---- 写入 bars_daily ----
    # 批量预加载当前范围内已有的行（READ，不持写锁），避免 N+1 SELECT
    existing_bars: dict = {
        r.trade_date: r
        for r in db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == symbol.id,
            BarDaily.trade_date >= start,
            BarDaily.trade_date <= end,
        )
        .all()
    }
    count = 0
    for _, row in df_daily.iterrows():
        td_raw = row["trade_date"]
        td_str = str(td_raw)
        # 兼容 Tushare 的两种日期格式：'20240115' 和 '2024-01-15'
        if len(td_str) == 8:
            trade_date = datetime.strptime(td_str, "%Y%m%d").date()
        else:
            trade_date = datetime.strptime(td_str[:10], "%Y-%m-%d").date()

        existing = existing_bars.get(trade_date)
        vol = int(float(row["vol"])) if row.get("vol") is not None else 0
        # Tushare daily 的 amount 单位已是「元」，与 index_daily（千元）不同，无需换算
        amt = float(row["amount"]) if row.get("amount") is not None else 0.0
        # 换手率：先按 '20240115' 格式查，再按 '2024-01-15' 格式查
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
            source=data_source,
        )
        if existing:
            # 覆盖更新（re-sync 时使用最新数据覆盖旧数据）
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

    db.commit()  # K 线批量提交

    # ---- 写入 adj_factors_daily ----
    adj_count = 0
    if adj_map:
        # 解析 adj_map 的日期键，批量预加载已有记录（同样 1 次 SELECT）
        adj_dates = []
        for td_str in adj_map.keys():
            if len(td_str) == 8:
                adj_dates.append(datetime.strptime(td_str, "%Y%m%d").date())
            else:
                adj_dates.append(datetime.strptime(td_str[:10], "%Y-%m-%d").date())
        existing_adjs: dict = {
            r.trade_date: r
            for r in db.query(AdjFactorDaily)
            .filter(
                AdjFactorDaily.symbol_id == symbol.id,
                AdjFactorDaily.trade_date.in_(adj_dates),
            )
            .all()
        }
        for td_str, af in adj_map.items():
            if len(td_str) == 8:
                adj_date = datetime.strptime(td_str, "%Y%m%d").date()
            else:
                adj_date = datetime.strptime(td_str[:10], "%Y-%m-%d").date()
            existing_adj = existing_adjs.get(adj_date)
            if existing_adj:
                existing_adj.adj_factor = Decimal(str(af))
                existing_adj.source = data_source
            else:
                db.add(
                    AdjFactorDaily(
                        symbol_id=symbol.id,
                        trade_date=adj_date,
                        adj_factor=Decimal(str(af)),
                        source=data_source,
                    )
                )
            adj_count += 1
        db.commit()  # 复权因子批量提交

    # 记录日志（区分 adj 是否失败）
    if adj_fetch_failed:
        w(f"upserted bar_rows={count} adj_factor_rows=0(FAILED) for {code}")
    else:
        w(f"upserted bar_rows={count} adj_factor_rows={adj_count} for {code}")

    # 注意：本函数不负责触发 indicator_pre_daily 重建 —— 理由同 _fetch_and_upsert_index_daily。
    return count, adj_fetch_failed


def fetch_all_a_stock_list(db: Session) -> list[dict]:
    """获取全量 A 股代码列表（用于 /api/symbols 下拉选单）。

    缓存策略：
    - 本地有缓存且今天已刷新过：直接返回本地数据，避免频繁消耗 Tushare 接口调用配额
    - 否则：调用 Tushare stock_basic，更新本地 symbols 表，并记录今日已同步标志

    若 Tushare 接口调用失败但本地有缓存，降级返回本地缓存（保证页面可用）。

    Returns:
        [{ts_code: str, name: str}, ...] 列表。
    """
    local_rows = db.query(Symbol).order_by(Symbol.ts_code.asc()).all()
    local_out = [{"ts_code": s.ts_code, "name": s.name} for s in local_rows]

    key = "stock_list_last_sync_date"
    last_sync = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    today_str = date.today().isoformat()

    # 有本地缓存且今天已同步过：直接返回，不消耗 Tushare 接口调用次数
    if local_out and last_sync and (last_sync.value or "").strip() == today_str:
        return local_out

    token = get_tushare_token()
    if not token:
        if local_out:
            return local_out  # token 未配置时降级返回本地缓存
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
        # list_status='L' 表示只拉上市中（Listed）的股票，不含退市/暂停上市
        df = pro.stock_basic(list_status="L", fields="ts_code,name")
    except Exception as ex:  # noqa: BLE001
        # 外部接口失败时优先降级返回本地缓存，保证 K 线页下拉选单可用
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

        # 同步写入 symbols 表（保证 K 线接口的外键关联可用）
        upsert_symbol_row_for_stock(db, ts_code, _str_or_none(name))

        out.append({"ts_code": ts_code, "name": name})

    # 记录今天已同步，下次同日请求直接走缓存
    if not last_sync:
        db.add(AppSetting(key=key, value=today_str))
    else:
        last_sync.value = today_str
    db.commit()
    return out


def _parse_list_date(s: str | None) -> date | None:
    """把 Tushare 返回的上市日期字符串（'20040817'）解析为 date，解析失败返回 None。"""
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
    """兼容旧路由名：等价于调用 incremental_sync_stock_list_meta（增量更新个股元数据）。

    force 参数保留以兼容旧调用，行为与不传 force 一致（每次都全量拉取并增量合并）。
    """
    _ = force  # 参数暂不使用，保留接口兼容
    return incremental_sync_stock_list_meta(db)


def incremental_sync_stock_list_meta(db: Session) -> dict:
    """从 Tushare stock_basic 增量合并 instrument_meta（个股元数据：名称、市场、交易所、上市日期）。

    增量逻辑：
    - 新出现的股票代码：插入新行
    - 已存在的代码：比较 name/list_date/market/exchange 是否有变化，有则更新

    注意：只处理个股（asset_type='stock'），指数元数据通过「更新指数列表」路由处理。
    同步同时也会更新 symbols 表（确保 /api/symbols 下拉与 instrument_meta 保持一致）。

    Returns:
        包含 stock_count/index_count/total/inserted_stocks/updated_stocks 等统计信息的字典。
    """
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
    # 拉取全部上市中个股的基础信息（list_status='L'）
    stock_df = pro.stock_basic(
        list_status="L",
        fields="ts_code,name,list_date,market,exchange",
    )
    inserted = 0
    updated = 0
    if stock_df is None or stock_df.empty:
        # Tushare 返回空数据（网络问题等），仅更新同步时间，不修改元数据
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

    # 预加载本地已有的个股元数据（减少循环中的 N+1 查询）
    meta_by_code: dict[str, InstrumentMeta] = {
        m.ts_code: m for m in db.query(InstrumentMeta).filter(InstrumentMeta.asset_type == "stock").all()
    }

    # 逐行比对：新增 or 更新
    for _, row in stock_df.iterrows():
        code = str(row.get("ts_code") or "").strip().upper()
        if not code:
            continue
        name = _str_or_none(row.get("name"))
        list_date = _parse_list_date(row.get("list_date"))
        market = _str_or_none(row.get("market"))
        exchange = _str_or_none(row.get("exchange"))

        if code not in meta_by_code:
            # 新股：插入 instrument_meta
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
            # 已有：检查字段是否有变化（如改名、调整市场分类等），有变化才更新
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

        # 同步更新 symbols 表：否则 /api/symbols 与 K 线选代码下拉可能为空
        upsert_symbol_row_for_stock(db, code, name)

    # 记录本次同步时间
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
    """调用 Tushare index_basic 接口，获取可供用户选择的指数列表（用于数据后台「指数」页签弹窗）。

    注意：此函数只从 Tushare 拉取候选列表，不写入数据库。
    用户在弹窗中勾选后，调用 apply_index_meta_selection 才正式写入。

    Args:
        market: 可选的市场过滤（如 'SSE'=上交所，'SZSE'=深交所），None 则返回全部
        limit: 最多返回条数（上限 8000）

    Returns:
        [{ts_code, name, market, publisher, list_date}, ...] 列表。
    """
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
    """将用户在弹窗中勾选的指数写入 instrument_meta 和 symbols 表。

    规则：
    - 代码已存在且 asset_type='index'：跳过（幂等，不重复插入）
    - 代码已存在且 asset_type='stock'：报错（股票和指数代码冲突，需人工核查）
    - 代码不存在：新建 instrument_meta（asset_type='index'）和 symbols 行

    Args:
        items: 用户勾选的指数列表，每项含 ts_code/name/list_date 等字段。

    Returns:
        {added: int, skipped: int}：新增条数和已存在跳过条数。
    """
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
                continue  # 已是指数，跳过
            # 已是个股，不允许重复登记为指数（数据矛盾）
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
        # 同步写入 symbols 表（指数 K 线接口依赖此表的外键）
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
    """历史兼容迁移：当 instrument_meta 为空时，从 symbols 表迁移数据（一次性操作）。

    背景：早期版本只有 symbols 表，没有 instrument_meta 表。
    升级后 instrument_meta 为空，数据后台和个股列表页会显示空白。
    此函数作为「降级兜底」：检测 instrument_meta 为空时，把 symbols 中的数据迁移过去，
    所有迁移行均标记为 asset_type='stock'（无法判断是否为指数，人工后续调整）。

    幂等：若 instrument_meta 已有数据则直接返回，不重复迁移。

    Returns:
        {inserted: int, skipped: bool}：skipped=True 表示已有数据，未执行迁移。
    """
    meta_count = db.query(InstrumentMeta).count()
    if meta_count > 0:
        return {"inserted": 0, "skipped": True}  # 已有元数据，无需迁移

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
                asset_type="stock",  # 迁移时统一标记为个股，指数需后续手动调整
                list_date=None,
                market=None,
                exchange=None,
            )
        )
        inserted += 1
    db.commit()
    return {"inserted": inserted, "skipped": False}
