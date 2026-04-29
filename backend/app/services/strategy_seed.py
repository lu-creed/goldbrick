"""系统预置策略种子(Phase 2:策略广场)。

12 个覆盖 4 类风格(逆势/趋势/突破/价值)的 backtest 类型策略,启动时 upsert 到 strategies 表
(user_id=NULL,对所有用户可读但不可删)。

与 user_indicator_seed.py 的分工:
  - user_indicator_seed.py:定义「可供策略引用的指标」(tpl_* 前缀)
  - strategy_seed.py(本模块):定义「具体的买卖策略」,引用 tpl_* 指标的子线

每个策略包含:
  - 基础字段(code, display_name, description)
  - 广场元数据(category, one_liner, long_description, good_for, bad_for)
  - 策略逻辑(indicator_code + sub_key + 买卖 op/threshold)
  - 预跑回测快照(preview_*,硬写,用户真跑会得到不同数值)

预跑数据说明:
  preview_* 字段是作者根据经验估算的「参考值」,不是真实跑出的数字。
  目的是让用户打开广场就能快速判断每个策略的大致风险收益特征。
  真正的数据要用户自己点「用这个策略」后在回测页跑出来。
  前端必须标注「参考值,非真实回测」以免误导。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Strategy, UserIndicator

log = logging.getLogger(__name__)


@dataclass
class PresetStrategy:
    """系统预置策略的完整描述(seed 定义层)。"""
    code: str                       # 如 "preset_rsi_oversold";作为 Strategy.code 存入 DB
    display_name: str
    description: str                # 列表页展示的简短说明

    category: str                   # 4 类之一:"逆势"/"趋势"/"突破"/"价值"
    one_liner: str                  # 人话一句话(广场卡片主文案)
    long_description: str           # 2-3 段详述(详情弹窗)
    good_for: list[str]             # 适合的场景
    bad_for: list[str]              # 不适合的场景(陷阱)

    # 策略逻辑(简化版单条件,本期 seed 均为「一个指标条件做买 + 同指标做卖」的结构)
    indicator_code: str             # 对应的 user_indicator code(如 "tpl_rsi")
    sub_key: str                    # 用哪条子线(如 "rsi12")
    buy_op: str                     # "gt" / "lt" 等
    buy_threshold: float
    sell_op: str
    sell_threshold: float
    max_positions: int = 3

    # 预跑回测快照(硬写参考值)
    preview_window: str = "2022-01-01 ~ 2024-12-31"
    preview_total_return_pct: float = 0.0
    preview_max_drawdown_pct: float = 0.0
    preview_total_trades: int = 0
    preview_win_rate: float = 50.0


# ── 12 个预置策略 ─────────────────────────────────────────────────
# 分类原则:
#   逆势:超买超卖/极端回归 — 适合震荡市
#   趋势:动量/金叉 — 适合单边市
#   突破:价量异常 — 适合行情启动期
#   价值:长期均值回归/深度超跌 — 追求稳健
_PRESETS: list[PresetStrategy] = [
    # ─── 逆势 3 ───────────────────────────────────────
    PresetStrategy(
        code="preset_rsi_oversold",
        display_name="RSI 超卖反弹",
        description="当股票超卖(RSI<30)时买入,涨到超买(RSI>70)时卖出。最经典的震荡市策略。",
        category="逆势",
        one_liner="当股票跌多了(RSI<30)就买入,反弹到涨多了(RSI>70)就卖出。",
        long_description=(
            "RSI 超卖反弹是最经典的均值回归策略之一。核心假设:股票涨多会跌、跌多会涨,"
            "RSI 指标能识别「涨得过度」和「跌得过度」的极端时刻。\n\n"
            "本策略使用 12 日 RSI(平衡灵敏度与稳定性):RSI 跌破 30 认为超卖,买入;"
            "回升到 70 以上认为超买,卖出。"
        ),
        good_for=["震荡市、盘整市", "波动较大的热点股", "想捕捉短期反弹的风格"],
        bad_for=["单边下跌大行情(RSI 会长期 < 30 却不反弹)", "基本面恶化的股票"],
        indicator_code="tpl_rsi", sub_key="rsi12",
        buy_op="lt", buy_threshold=30.0,
        sell_op="gt", sell_threshold=70.0,
        max_positions=3,
        preview_total_return_pct=18.2, preview_max_drawdown_pct=-11.5,
        preview_total_trades=42, preview_win_rate=57.0,
    ),
    PresetStrategy(
        code="preset_kdj_bottom",
        display_name="KDJ 底部金叉",
        description="KDJ 的 J 值跌到超卖区(J<20)时买入,冲高到超买区(J>80)时卖出。",
        category="逆势",
        one_liner="KDJ 的 J 值跌到极端超卖区就买入,冲到极端超买就卖出。",
        long_description=(
            "KDJ 的 J 值比 RSI 更敏感,能更快识别极端。\n\n"
            "J 值 < 20 为超卖区,常对应短期底部;J > 80 为超买区,常对应短期顶部。"
            "因为 J 值波动剧烈,该策略触发频繁,适合愿意多做短线的用户。"
        ),
        good_for=["短线交易风格", "波动大的个股"],
        bad_for=["强势单边行情(J 值会长期钝化)", "不喜欢频繁交易的人"],
        indicator_code="tpl_kdj_j", sub_key="j",
        buy_op="lt", buy_threshold=20.0,
        sell_op="gt", sell_threshold=80.0,
        max_positions=5,
        preview_total_return_pct=22.8, preview_max_drawdown_pct=-14.2,
        preview_total_trades=68, preview_win_rate=54.0,
    ),
    PresetStrategy(
        code="preset_bias_regression",
        display_name="BIAS 乖离回归",
        description="当股价明显低于 12 日均线(BIAS < -8%)时买入,回升到均线上方 5% 时卖出。",
        category="逆势",
        one_liner="股价跌到远低于 12 日均线(乖离 < -8%)就买,涨回均线上方就卖。",
        long_description=(
            "BIAS(乖离率)衡量股价偏离均线的程度。正常情况下股价围绕均线波动,"
            "过度偏离终将回归 — 这就是「均值回归」。\n\n"
            "本策略用 12 日 BIAS < -8% 作为「跌过头」的信号买入,BIAS > 5% 作为「反弹到位」的卖出。"
            "阈值比较保守,信号较稀疏但质量通常较高。"
        ),
        good_for=["长期震荡的大盘蓝筹", "基本面稳定的白马股"],
        bad_for=["趋势性个股(乖离扩大后还会继续扩大)"],
        indicator_code="tpl_bias", sub_key="bias12",
        buy_op="lt", buy_threshold=-8.0,
        sell_op="gt", sell_threshold=5.0,
        max_positions=3,
        preview_total_return_pct=14.6, preview_max_drawdown_pct=-9.3,
        preview_total_trades=28, preview_win_rate=60.0,
    ),

    # ─── 趋势 3 ───────────────────────────────────────
    PresetStrategy(
        code="preset_ma_cross",
        display_name="均线金叉死叉",
        description="MA5 上穿 MA20(金叉)时买入,MA5 下穿 MA20(死叉)时卖出。最经典趋势跟随策略。",
        category="趋势",
        one_liner="短期均线(MA5)上穿长期均线(MA20)就买,下穿就卖。",
        long_description=(
            "均线金叉是最经典的趋势跟随策略。当短线均线上穿长线均线时,认为「多头力量已战胜空头」,"
            "趋势开启;反之为趋势结束。\n\n"
            "本策略用 MA5-MA20 的差值:>0 即金叉买入,<0 即死叉卖出。"
            "简单直接,但在震荡市会频繁给出假信号,需结合市场大环境判断。"
        ),
        good_for=["单边趋势行情", "板块轮动中的热点股", "不想盯盘的稳健型用户"],
        bad_for=["长期震荡的股票(会被反复打脸)", "超短线交易"],
        indicator_code="tpl_ma_cross", sub_key="diff",
        buy_op="gt", buy_threshold=0.0,
        sell_op="lt", sell_threshold=0.0,
        max_positions=5,
        preview_total_return_pct=25.3, preview_max_drawdown_pct=-18.7,
        preview_total_trades=35, preview_win_rate=48.0,
    ),
    PresetStrategy(
        code="preset_macd_bar",
        display_name="MACD 柱反转",
        description="MACD 柱转红(>0)时买入,转绿(<0)时卖出。经典的 MACD 趋势跟随策略。",
        category="趋势",
        one_liner="MACD 柱从绿变红(动能转多)买入,从红变绿(动能转空)卖出。",
        long_description=(
            "MACD 柱状图反映多空动能。柱子由绿转红,表示上涨动能超过下跌;由红转绿则相反。\n\n"
            "本策略在 MACD 柱 > 0 时买入,< 0 时卖出。相比 MA 金叉,MACD 对动能变化更敏感,"
            "可以更早识别趋势转折,但也更容易在震荡市被假信号迷惑。"
        ),
        good_for=["中期趋势交易", "中大盘蓝筹"],
        bad_for=["短线(MACD 本身滞后)", "低流动性股票"],
        indicator_code="tpl_macd_bar", sub_key="bar",
        buy_op="gt", buy_threshold=0.0,
        sell_op="lt", sell_threshold=0.0,
        max_positions=5,
        preview_total_return_pct=21.1, preview_max_drawdown_pct=-16.8,
        preview_total_trades=38, preview_win_rate=50.0,
    ),
    PresetStrategy(
        code="preset_trix_cross",
        display_name="TRIX 零轴突破",
        description="TRIX 上穿零轴(趋势由空转多)时买入,下穿零轴时卖出。中长期趋势策略。",
        category="趋势",
        one_liner="TRIX 穿越零轴作为中长期趋势转折信号,简单稳定。",
        long_description=(
            "TRIX 经过三重指数平滑,对短期波动不敏感,主要捕捉中长期趋势。\n\n"
            "零轴突破是 TRIX 最经典的信号:> 0 表示中长期多头,< 0 表示中长期空头。"
            "信号触发频率低,一旦触发趋势性强。适合不想频繁操作的长线用户。"
        ),
        good_for=["中长线配置", "大盘蓝筹", "不盯盘的稳健型用户"],
        bad_for=["短线捕捉", "震荡市(TRIX 在零轴附近会反复穿越)"],
        indicator_code="tpl_trix", sub_key="trix12",
        buy_op="gt", buy_threshold=0.0,
        sell_op="lt", sell_threshold=0.0,
        max_positions=3,
        preview_total_return_pct=19.4, preview_max_drawdown_pct=-13.2,
        preview_total_trades=18, preview_win_rate=55.0,
    ),

    # ─── 突破 3 ───────────────────────────────────────
    PresetStrategy(
        code="preset_boll_bounce",
        display_name="布林下轨反弹",
        description="股价触及布林带下轨(在通道底部 10% 以内)时买入,反弹到通道上方 80% 时卖出。",
        category="突破",
        one_liner="股价跌到布林带下轨附近(支撑位)时买入,反弹到上轨附近卖出。",
        long_description=(
            "布林带是价格的「统计边界」,股价触及下轨往往意味着短期超卖。\n\n"
            "本策略在「布林位置 < 0.1」(距下轨 10% 以内)买入,「布林位置 > 0.8」(距上轨 20% 以内)卖出。"
            "在震荡市和带量反转的个股上表现不错,但单边破位时要小心 — 下轨不是绝对支撑。"
        ),
        good_for=["震荡市", "有明显波动区间的股票", "愿意做波段的用户"],
        bad_for=["单边破位下跌", "刚启动的强势趋势股"],
        indicator_code="tpl_boll_pos", sub_key="pos",
        buy_op="lt", buy_threshold=0.1,
        sell_op="gt", sell_threshold=0.8,
        max_positions=3,
        preview_total_return_pct=16.8, preview_max_drawdown_pct=-12.1,
        preview_total_trades=32, preview_win_rate=58.0,
    ),
    PresetStrategy(
        code="preset_vol_breakout",
        display_name="量比放量突破",
        description="成交量放大到 20 日均量 2 倍以上(量比 > 2)时买入,缩量(量比 < 0.8)时卖出。",
        category="突破",
        one_liner="成交量突然放大到平时的 2 倍以上时买入,跟随主力资金。",
        long_description=(
            "量比是当日成交量与 20 日均量的比值。> 2 表示显著放量 — 要么是主力加仓,要么是消息驱动。\n\n"
            "本策略认为放量常伴随趋势启动,买入;缩量则是关注度下降,卖出。\n\n"
            "⚠️ 注意:高位放量可能是主力出货,不是所有放量都是买点。最好配合其它指标确认方向。"
        ),
        good_for=["消息驱动的热点股", "日涨停板相关标的", "主力资金关注的板块龙头"],
        bad_for=["高位放量可能是出货", "低流动性股票(量比数据失真)"],
        indicator_code="tpl_vol_ratio", sub_key="vol_ratio",
        buy_op="gt", buy_threshold=2.0,
        sell_op="lt", sell_threshold=0.8,
        max_positions=5,
        preview_total_return_pct=24.5, preview_max_drawdown_pct=-22.3,
        preview_total_trades=56, preview_win_rate=45.0,
    ),
    PresetStrategy(
        code="preset_cci_extreme",
        display_name="CCI 极端反转",
        description="CCI 跌破 -100(超卖)时买入,突破 +100(超买)时卖出。捕捉极端偏离后的均值回归。",
        category="突破",
        one_liner="CCI 跌到 -100 以下(超卖)就买,冲到 +100 以上(超买)就卖。",
        long_description=(
            "CCI 衡量价格偏离统计均值的程度,擅长捕捉「极端偏离」这种稀有但高质量的反转机会。\n\n"
            "< -100 为超卖区,> +100 为超买区。虽然理论上可到 ±300 以上,但 ±100 阈值已经过滤掉大部分噪音。\n\n"
            "本策略信号稀疏但质量高,适合有耐心等机会的用户。"
        ),
        good_for=["耐心等机会的长线用户", "震荡偏多行情"],
        bad_for=["强单边趋势(CCI 可长期停留在极端区)"],
        indicator_code="tpl_cci", sub_key="cci14",
        buy_op="lt", buy_threshold=-100.0,
        sell_op="gt", sell_threshold=100.0,
        max_positions=3,
        preview_total_return_pct=17.2, preview_max_drawdown_pct=-13.5,
        preview_total_trades=24, preview_win_rate=61.0,
    ),

    # ─── 价值/均值回归 3 ──────────────────────────────
    PresetStrategy(
        code="preset_roc_momentum",
        display_name="ROC 动量突破",
        description="12 日 ROC(价格变化率)> +5% 时买入,< -3% 时卖出。顺势捕捉动量加速期。",
        category="价值",
        one_liner="12 日股价涨幅超过 5% 才买入,明显回撤超过 3% 就止损。",
        long_description=(
            "ROC 衡量 12 日股价变化率。> +5% 认为动量已经确立,> 0 不够 — 这是避免在微涨时上车被震出局。\n\n"
            "与一般趋势策略不同,本策略**买入门槛更高、止损更早**,适合不想赌反转、只吃趋势中段的风格。"
        ),
        good_for=["板块轮动的热点主升段", "不想做反转交易的顺势用户"],
        bad_for=["持续震荡的股票", "过度追涨风险 — 进场时股价已涨过不少"],
        indicator_code="tpl_roc", sub_key="roc12",
        buy_op="gt", buy_threshold=5.0,
        sell_op="lt", sell_threshold=-3.0,
        max_positions=3,
        preview_total_return_pct=15.9, preview_max_drawdown_pct=-17.2,
        preview_total_trades=34, preview_win_rate=47.0,
    ),
    PresetStrategy(
        code="preset_bias_deep",
        display_name="BIAS 深度超卖",
        description="BIAS 12 日 < -15%(深度超卖)时买入,回到 0 以上时卖出。只捕捉极端抄底机会。",
        category="价值",
        one_liner="只在股价严重跌破均线(超跌 15% 以上)时买入,回归均线就卖。",
        long_description=(
            "这是「BIAS 乖离回归」策略的**深度版**,阈值更严格。\n\n"
            "只在 BIAS < -15% 时才买入 — 这通常对应大跌后的底部区域,对应的机会比较稀少。"
            "卖出阈值设为 BIAS > 0(回到均线上方)即走人,不贪图后续涨幅。\n\n"
            "追求**高质量信号 + 稳健兑现**。交易少,但每笔心理压力小。"
        ),
        good_for=["耐心型长线用户", "追求稳健收益的保守风格"],
        bad_for=["短线交易", "追求高收益的激进风格"],
        indicator_code="tpl_bias", sub_key="bias12",
        buy_op="lt", buy_threshold=-15.0,
        sell_op="gt", sell_threshold=0.0,
        max_positions=3,
        preview_total_return_pct=12.4, preview_max_drawdown_pct=-7.8,
        preview_total_trades=15, preview_win_rate=67.0,
    ),
    PresetStrategy(
        code="preset_rsi_extreme",
        display_name="RSI 极端抄底",
        description="RSI 12 日 < 20(极端超卖)时买入,RSI > 60 时卖出。追求低频高胜率。",
        category="价值",
        one_liner="只在 RSI 跌到极端超卖(< 20)时才出手,反弹到 60 就兑现,不贪心。",
        long_description=(
            "这是「RSI 超卖反弹」的**保守版**:把买入阈值从 30 收紧到 20,"
            "把卖出阈值从 70 降到 60。\n\n"
            "信号触发极少(一年可能就几次),但每次触发通常对应相对真实的底部。"
            "卖出早走也牺牲了部分收益空间,换取更高胜率和更快的资金周转。\n\n"
            "适合不想频繁操作、但不完全放弃短期机会的用户。"
        ),
        good_for=["低频交易风格", "心理承受力弱、追求高胜率的用户"],
        bad_for=["追求高收益", "想充分享受反弹涨幅"],
        indicator_code="tpl_rsi", sub_key="rsi12",
        buy_op="lt", buy_threshold=20.0,
        sell_op="gt", sell_threshold=60.0,
        max_positions=3,
        preview_total_return_pct=11.8, preview_max_drawdown_pct=-6.9,
        preview_total_trades=12, preview_win_rate=71.0,
    ),
]


def _build_logic_json(user_indicator_id: int, sub_key: str, op: str, threshold: float) -> str:
    """构造单条件的 StrategyLogic JSON(买/卖两侧都用这个形状)。"""
    logic = {
        "conditions": [{
            "id": 1,
            "user_indicator_id": user_indicator_id,
            "sub_key": sub_key,
            "compare_op": op,
            "threshold": threshold,
        }],
        "groups": [{"id": "G1", "condition_ids": [1]}],
        "combiner": {"ref": "G1"},
        "primary_condition_id": 1,
    }
    return json.dumps(logic, ensure_ascii=False)


def ensure_default_strategies(db: Session) -> None:
    """启动时确保 12 个系统预置策略存在(user_id=NULL)。已存在按 code 跳过,不覆盖用户对同名字段的修改。

    幂等:已存在同 code + user_id=NULL 的策略不重写(即使 preview 数据有更新)。
    若要强制刷新,需要手动删库或改 code。
    """
    # 建立 user_indicator.code → id 的映射(只查 user_id=NULL 的系统预置指标)
    ind_rows = (
        db.query(UserIndicator.code, UserIndicator.id)
        .filter(UserIndicator.user_id.is_(None))
        .all()
    )
    code_to_id = {row.code: row.id for row in ind_rows}

    added = 0
    skipped = 0
    missing_indicators: list[str] = []

    for preset in _PRESETS:
        uid = code_to_id.get(preset.indicator_code)
        if uid is None:
            missing_indicators.append(preset.indicator_code)
            continue

        # 已存在就跳过(按 user_id=NULL + code 唯一)
        existing = (
            db.query(Strategy)
            .filter(Strategy.user_id.is_(None), Strategy.code == preset.code)
            .one_or_none()
        )
        if existing:
            skipped += 1
            continue

        row = Strategy(
            user_id=None,               # 系统预置
            code=preset.code,
            display_name=preset.display_name,
            description=preset.description,
            kind="backtest",
            buy_logic_json=_build_logic_json(uid, preset.sub_key, preset.buy_op, preset.buy_threshold),
            sell_logic_json=_build_logic_json(uid, preset.sub_key, preset.sell_op, preset.sell_threshold),
        )
        db.add(row)
        added += 1

    if missing_indicators:
        log.warning(
            "strategy_seed: 以下预置指标未找到,对应策略已跳过: %s。"
            "请先启动应用让 user_indicator_seed 跑一遍,再重启。",
            missing_indicators,
        )

    if added > 0 or skipped > 0:
        db.commit()
        log.info("ensure_default_strategies: 新增 %d 个,跳过 %d 个(已存在)", added, skipped)


# 给 gallery API 用:不查库,直接从 _PRESETS 返回广场元数据
def get_preset_by_code(code: str) -> PresetStrategy | None:
    for p in _PRESETS:
        if p.code == code:
            return p
    return None


def list_presets() -> list[PresetStrategy]:
    return list(_PRESETS)
