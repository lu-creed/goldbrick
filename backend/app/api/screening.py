"""条件选股 API（路径前缀 /api/screening/）。

提供一个接口：POST /api/screening/run
- 基于用户自定义指标，在指定交易日对全市场个股进行条件筛选
- 例如：「MACD 死叉指标 > 0」或「自定义趋势强度 < -5」的全市场股票列表
- 结果按指标值降序排列，帮助用户快速找到满足条件的标的

核心扫描逻辑在 app/services/screening_runner.py 中实现（分批查询 + 指标计算）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ScreeningRunIn, ScreeningRunOut, ScreeningStockRow
from app.services.screening_runner import run_screen

router = APIRouter(prefix="/screening", tags=["screening"])


@router.post("/run", response_model=ScreeningRunOut)
def screening_run(body: ScreeningRunIn, db: Session = Depends(get_db)):
    """在指定交易日对全市场个股执行条件选股扫描。

    扫描流程（约 6000 只个股，分批进行）：
    1. 查出该交易日有日线的全部个股列表
    2. 每批 450 只，一次性加载「预热期~交易日」的历史 K 线
    3. 对每只股票用指标引擎计算最后一根 K 线（即交易日当天）的指标值
    4. 与阈值比较（compare_op），满足条件加入结果
    5. 结果按指标值降序排列

    Args（均在 body 中传入，见 ScreeningRunIn schema）：
        trade_date: 要扫描的交易日截面（只看当天的指标值）。
        user_indicator_id: 使用哪个自定义指标（必须是已保存的）。
        sub_key: DSL 指标选哪条子线参与选股；旧版 expr 指标传空字符串。
        compare_op: 比较运算符，如 'gt'（大于）、'lt'（小于）、'gte'（大于等于）等。
        threshold: 阈值，如 0（表示「指标值 > 0」）。
        max_scan: 最多扫描多少只股票（默认 6000，超时保护）。

    Returns:
        ScreeningRunOut，包含：
        - scanned: 实际扫描的股票数
        - matched: 命中条件的股票数
        - items: 命中的股票列表（含代码、名称、收盘价、涨跌幅、指标值）
    """
    try:
        raw = run_screen(
            db,
            trade_date=body.trade_date,
            user_indicator_id=body.user_indicator_id,
            sub_key=body.sub_key or "",
            compare_op=body.compare_op,
            threshold=body.threshold,
            max_scan=body.max_scan,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    items = [ScreeningStockRow(**x) for x in raw["items"]]
    return ScreeningRunOut(
        trade_date=raw["trade_date"],
        user_indicator_id=raw.get("user_indicator_id"),
        sub_key=raw.get("sub_key"),
        compare_op=raw.get("compare_op"),
        threshold=raw.get("threshold"),
        scanned=raw["scanned"],
        matched=raw["matched"],
        note=raw.get("note"),
        items=items,
    )
