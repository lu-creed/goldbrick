"""指标库种子数据：启动时写入，已存在则跳过。"""
from sqlalchemy.orm import Session

from app.models import Indicator, IndicatorParam, IndicatorSubIndicator

# 格式：(name, display_name, description, params_list, sub_list)
# params_list: [(name, description, default_value), ...]
# sub_list: [(name, description), ...]
# 格式：(name, display_name, desc, params_list, sub_list)
# sub_list 元素：(name, description, can_be_price)
# can_be_price=True 的子指标才能在回测中作为买入/卖出价格基准
_SEED = [
    (
        "MA", "移动平均线",
        "以N个周期收盘价的算术平均值构成的曲线，是最常用的趋势跟踪指标。参数已锁定，提供5/10/20/30/60五条均线。",
        [("N", "均线周期（已锁定，不可修改）", "5/10/20/30/60")],
        [
            ("MA5",  "5日移动平均线",  True),
            ("MA10", "10日移动平均线", True),
            ("MA20", "20日移动平均线", True),
            ("MA30", "30日移动平均线", True),
            ("MA60", "60日移动平均线", True),
        ],
    ),
    (
        "KDJ", "随机指标",
        "由K、D、J三条线组成，通过比较收盘价与价格区间衡量超买超卖状态。参数已锁定：N=9，M1=3，M2=3。",
        [
            ("N",  "RSV计算周期（已锁定）", "9"),
            ("M1", "K值平滑系数分母（已锁定）", "3"),
            ("M2", "D值平滑系数分母（已锁定）", "3"),
        ],
        [
            ("K", "快速随机线，反应较灵敏", False),
            ("D", "慢速随机线，K的移动平均", False),
            ("J", "K与D的偏差放大值，超买超卖信号更灵敏", False),
        ],
    ),
    (
        "BOLL", "布林带",
        "以N期收盘价均值为中轨，上下各扩展2个标准差，形成价格通道。参数已锁定：N=20，sigma=2。",
        [
            ("N",     "均值计算周期（已锁定）", "20"),
            ("sigma", "标准差倍数（已锁定）", "2"),
        ],
        [
            ("UPPER", "布林上轨，压力参考线", True),
            ("MID",   "布林中轨，20日移动均线", True),
            ("LOWER", "布林下轨，支撑参考线", True),
        ],
    ),
    (
        "MACD", "指数平滑异同移动平均线",
        "通过快慢两条EMA的差值（DIF）及其信号线（DEA）捕捉趋势转折。参数已锁定：fast=12，slow=26，signal=9。",
        [
            ("fast",   "快线EMA周期（已锁定）", "12"),
            ("slow",   "慢线EMA周期（已锁定）", "26"),
            ("signal", "信号线（DEA）周期（已锁定）", "9"),
        ],
        [
            ("DIF",   "快线与慢线EMA之差", False),
            ("DEA",   "DIF的信号线（DIF的M日EMA）", False),
            ("MACD柱", "2×(DIF-DEA)，柱状图展示动能", False),
        ],
    ),
    (
        "EXPMA", "指数移动平均线",
        "对近期价格给予更高权重的加权均线，相比MA对价格变化更敏感。参数已锁定，提供12/26两条线。",
        [("N", "EMA计算周期（已锁定）", "12/26")],
        [
            ("EXPMA12", "12日指数移动平均线", False),
            ("EXPMA26", "26日指数移动平均线", False),
        ],
    ),
    (
        "STOCK_DATA", "个股数据",
        "个股本身的原始行情数据，作为买入/卖出条件的基础数据源。",
        [],
        [
            ("close",         "当日收盘价",      True),
            ("open",          "当日开盘价",      True),
            ("high",          "当日最高价",      True),
            ("low",           "当日最低价",      True),
            ("turnover_rate", "当日换手率（%）", False),
            ("volume",        "当日成交量（手）", False),
        ],
    ),
]


def seed_indicators(db: Session, force: bool = False) -> None:
    """写入种子数据。force=True 时先删除同名旧数据再重写（用于数据修复）。"""
    for name, display_name, desc, params, subs in _SEED:
        exists = db.query(Indicator).filter(Indicator.name == name).one_or_none()
        if exists:
            if not force:
                continue
            # 级联删除旧记录（params / sub_indicators 已配置 cascade）
            db.delete(exists)
            db.flush()

        ind = Indicator(name=name, display_name=display_name, description=desc)
        db.add(ind)
        db.flush()
        for p_name, p_desc, p_default in params:
            db.add(IndicatorParam(
                indicator_id=ind.id,
                name=p_name,
                description=p_desc,
                default_value=p_default,
            ))
        for s_name, s_desc, s_price in subs:
            db.add(IndicatorSubIndicator(
                indicator_id=ind.id,
                name=s_name,
                description=s_desc,
                can_be_price=s_price,
            ))
    db.commit()
