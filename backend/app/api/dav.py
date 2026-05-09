"""大V看板 API（路径前缀 /api/dav/）。

按 Mr. Dang 的 ABCD 分类框架管理自选股，手动维护派息率与 EPS，
自动从本地 bars_daily 取最新收盘价并计算预期股息率。

积分升级后可接入 Tushare daily_basic / dividend / fina_indicator，
届时 auto_payout_ratio / auto_eps 字段会由同步任务自动填充，
手动值优先级保持高于自动值，本接口无需改动。

接口列表：
  GET    /api/dav/stocks                        全部看板股票（含最新价 + 预期股息率）
  POST   /api/dav/stocks                        添加股票到看板
  PATCH  /api/dav/stocks/{ts_code}              更新分类/派息率/EPS/备注
  DELETE /api/dav/stocks/{ts_code}              从看板移除
  GET    /api/dav/stocks/search                 搜索本地已知股票（供添加时下拉选）
  GET    /api/dav/stocks/{ts_code}/auto-payout  实时从 AKShare 拉取派息率/EPS
  POST   /api/dav/auto-classify                 按规则批量自动分类
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DavStockWatch, InstrumentMeta
from app.auth import get_current_user

router = APIRouter(prefix="/dav", tags=["dav"])


# ── Schemas ────────────────────────────────────────────────────────────────

class DavStockIn(BaseModel):
    ts_code: str
    dav_class: Optional[str] = Field(None, pattern="^[ABCD]$")
    manual_payout_ratio: Optional[float] = None   # 近两年平均派息率 %
    manual_eps: Optional[float] = None            # 预测全年 EPS 元
    notes: Optional[str] = None


class DavStockPatch(BaseModel):
    dav_class: Optional[str] = Field(None, pattern="^[ABCD]$")
    manual_payout_ratio: Optional[float] = None
    manual_eps: Optional[float] = None
    notes: Optional[str] = None


class DavStockOut(BaseModel):
    ts_code: str
    name: Optional[str]
    dav_class: Optional[str]
    latest_price: Optional[float]      # 本地 bars_daily 最新收盘价
    manual_payout_ratio: Optional[float]
    manual_eps: Optional[float]
    auto_payout_ratio: Optional[float]  # AKShare 自动填充的派息率（手填优先）
    auto_eps: Optional[float]           # AKShare 自动填充的 EPS（手填优先）
    expected_yield: Optional[float]    # 派息率% × EPS ÷ 股价 × 100（%），None 表示数据不足
    data_complete: bool                # True = 三个数据都有，可自动计算
    notes: Optional[str]


class SearchItem(BaseModel):
    ts_code: str
    name: Optional[str]


# ── 内部工具 ────────────────────────────────────────────────────────────────

def _latest_price(ts_code: str, db: Session) -> Optional[float]:
    """从 bars_daily 查该股票最新一日收盘价。"""
    row = db.execute(text("""
        SELECT CAST(b.close AS REAL)
        FROM bars_daily b
        JOIN symbols s ON s.id = b.symbol_id
        WHERE s.ts_code = :code
        ORDER BY b.trade_date DESC
        LIMIT 1
    """), {"code": ts_code}).fetchone()
    return float(row[0]) if row else None


def _compute_yield(payout_ratio: Optional[float], eps: Optional[float],
                   price: Optional[float]) -> Optional[float]:
    """预期股息率 = 派息率(%) × EPS ÷ 股价 × 100，结果单位也是 %。"""
    if payout_ratio is None or eps is None or price is None or price <= 0:
        return None
    return round(payout_ratio / 100.0 * eps / price * 100.0, 4)


def _to_out(row: DavStockWatch, db: Session) -> DavStockOut:
    meta = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == row.ts_code).first()
    name = meta.name if meta else None
    price = _latest_price(row.ts_code, db)
    manual_pr = float(row.manual_payout_ratio) if row.manual_payout_ratio is not None else None
    manual_eps = float(row.manual_eps) if row.manual_eps is not None else None
    auto_pr = float(row.auto_payout_ratio) if row.auto_payout_ratio is not None else None
    auto_eps = float(row.auto_eps) if row.auto_eps is not None else None
    # 手填优先，手填为空时使用自动值
    eff_pr = manual_pr if manual_pr is not None else auto_pr
    eff_eps = manual_eps if manual_eps is not None else auto_eps
    return DavStockOut(
        ts_code=row.ts_code,
        name=name,
        dav_class=row.dav_class,
        latest_price=price,
        manual_payout_ratio=manual_pr,
        manual_eps=manual_eps,
        auto_payout_ratio=auto_pr,
        auto_eps=auto_eps,
        expected_yield=_compute_yield(eff_pr, eff_eps, price),
        data_complete=all(x is not None for x in [eff_pr, eff_eps, price]),
        notes=row.notes,
    )


# ── 路由 ────────────────────────────────────────────────────────────────────

@router.get("/stocks/search", response_model=List[SearchItem])
def search_stocks(q: str = Query("", max_length=20), _user=Depends(get_current_user), db: Session = Depends(get_db)):
    """在本地 instrument_meta 中按代码或名称模糊搜索，供添加时下拉选用。"""
    like = f"%{q}%"
    rows = db.query(InstrumentMeta).filter(
        InstrumentMeta.asset_type == "stock",
        (InstrumentMeta.ts_code.ilike(like) | InstrumentMeta.name.ilike(like)),
    ).order_by(InstrumentMeta.ts_code).limit(20).all()
    return [SearchItem(ts_code=r.ts_code, name=r.name) for r in rows]


@router.get("/stocks", response_model=List[DavStockOut])
def list_stocks(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """返回当前用户看板全部股票，含最新价与预期股息率。"""
    rows = db.query(DavStockWatch).filter(
        DavStockWatch.user_id == current_user.id,
    ).order_by(DavStockWatch.dav_class.nullslast(), DavStockWatch.ts_code).all()
    return [_to_out(r, db) for r in rows]


@router.post("/stocks", response_model=DavStockOut, status_code=201)
def add_stock(body: DavStockIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """添加一只股票到大V看板，同一用户同一代码不可重复添加。"""
    if db.query(DavStockWatch).filter(
        DavStockWatch.user_id == current_user.id,
        DavStockWatch.ts_code == body.ts_code,
    ).first():
        raise HTTPException(status_code=409, detail=f"{body.ts_code} 已在看板中")
    row = DavStockWatch(
        user_id=current_user.id,
        ts_code=body.ts_code,
        dav_class=body.dav_class,
        manual_payout_ratio=body.manual_payout_ratio,
        manual_eps=body.manual_eps,
        notes=body.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row, db)


@router.patch("/stocks/{ts_code}", response_model=DavStockOut)
def update_stock(ts_code: str, body: DavStockPatch, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """更新分类、派息率、EPS 或备注，只传需要修改的字段。"""
    row = db.query(DavStockWatch).filter(
        DavStockWatch.user_id == current_user.id,
        DavStockWatch.ts_code == ts_code,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到该股票")
    if body.dav_class is not None:
        row.dav_class = body.dav_class
    if body.manual_payout_ratio is not None:
        row.manual_payout_ratio = body.manual_payout_ratio
    if body.manual_eps is not None:
        row.manual_eps = body.manual_eps
    if body.notes is not None:
        row.notes = body.notes
    db.commit()
    db.refresh(row)
    return _to_out(row, db)


@router.delete("/stocks/{ts_code}", status_code=204)
def remove_stock(ts_code: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """从看板移除一只股票。"""
    row = db.query(DavStockWatch).filter(
        DavStockWatch.user_id == current_user.id,
        DavStockWatch.ts_code == ts_code,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="未找到该股票")
    db.delete(row)
    db.commit()


# ── 自动获取派息率/EPS ──────────────────────────────────────────────────────────

class AutoPayoutOut(BaseModel):
    payout_ratio: Optional[float]  # 近两年平均派息率 %
    eps: Optional[float]           # 最新年度 EPS 元/股


@router.get("/stocks/{ts_code}/auto-payout", response_model=AutoPayoutOut)
def get_auto_payout(ts_code: str, _user=Depends(get_current_user)):
    """实时调用 AKShare 拉取个股分红历史，计算近两年平均派息率和最新 EPS。
    结果不写入数据库，由前端决定是否填入手动字段。
    注意：网络请求约 1-5 秒，前端需显示加载态。
    """
    from app.services.akshare_fundamentals import fetch_payout_ratio_eps
    try:
        payout_ratio, eps = fetch_payout_ratio_eps(ts_code)
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"AKShare 调用失败：{ex}") from ex
    return AutoPayoutOut(payout_ratio=payout_ratio, eps=eps)


# ── 自动分类 ────────────────────────────────────────────────────────────────────

class AutoClassifyRule(BaseModel):
    """单条自动分类规则：当 expected_yield >= yield_min 且 pe_ttm <= pe_max 时分为 target_class。
    规则按顺序匹配，第一条匹配中的规则生效（类似 if/elif/else）。
    """
    yield_min: Optional[float] = None    # 预期股息率 >= 该值（%）
    pe_max: Optional[float] = None       # PE ≤ 该值（None = 不限）
    target_class: str                    # 目标分类 A/B/C/D


class AutoClassifyIn(BaseModel):
    rules: List[AutoClassifyRule]
    overwrite: bool = False  # False = 只分类"未分类"股票；True = 强制覆盖已有分类


class AutoClassifyResult(BaseModel):
    classified: int   # 本次成功分类的数量
    skipped: int      # 跳过（overwrite=False 且已有分类）的数量
    details: List[dict]  # 每只股票的分类结果，便于前端展示


@router.post("/auto-classify", response_model=AutoClassifyResult)
def auto_classify(body: AutoClassifyIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """按用户提供的规则批量自动分类看板股票。
    规则按顺序匹配，第一条命中的规则生效；没有规则匹配的股票不改变分类。
    """
    valid_classes = {"A", "B", "C", "D"}
    for r in body.rules:
        if r.target_class not in valid_classes:
            raise HTTPException(status_code=422, detail=f"target_class 必须是 A/B/C/D，收到：{r.target_class}")

    rows = db.query(DavStockWatch).filter(
        DavStockWatch.user_id == current_user.id,
    ).all()

    classified = 0
    skipped = 0
    details = []

    for row in rows:
        if not body.overwrite and row.dav_class is not None:
            skipped += 1
            details.append({"ts_code": row.ts_code, "action": "skipped", "dav_class": row.dav_class})
            continue

        # 计算该股票当前预期股息率（用于规则匹配）
        price = _latest_price(row.ts_code, db)
        eff_pr = float(row.manual_payout_ratio) if row.manual_payout_ratio is not None else (
            float(row.auto_payout_ratio) if row.auto_payout_ratio is not None else None
        )
        eff_eps = float(row.manual_eps) if row.manual_eps is not None else (
            float(row.auto_eps) if row.auto_eps is not None else None
        )
        current_yield = _compute_yield(eff_pr, eff_eps, price)

        # 查 PE（从 fundamental_daily 取最新记录）
        pe_row = db.execute(text("""
            SELECT pe_ttm FROM fundamental_daily
            WHERE ts_code = :code
            ORDER BY trade_date DESC LIMIT 1
        """), {"code": row.ts_code}).fetchone()
        current_pe = float(pe_row[0]) if pe_row and pe_row[0] is not None else None

        matched_class = None
        for rule in body.rules:
            yield_ok = (rule.yield_min is None) or (current_yield is not None and current_yield >= rule.yield_min)
            pe_ok = (rule.pe_max is None) or (current_pe is not None and current_pe <= rule.pe_max)
            if yield_ok and pe_ok:
                matched_class = rule.target_class
                break

        if matched_class is not None:
            old_class = row.dav_class
            row.dav_class = matched_class
            classified += 1
            details.append({"ts_code": row.ts_code, "action": "classified",
                            "from": old_class, "to": matched_class,
                            "yield": current_yield, "pe": current_pe})
        else:
            skipped += 1
            details.append({"ts_code": row.ts_code, "action": "no_match",
                            "dav_class": row.dav_class, "yield": current_yield, "pe": current_pe})

    db.commit()
    return AutoClassifyResult(classified=classified, skipped=skipped, details=details)
