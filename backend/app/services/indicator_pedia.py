"""内置指标人话百科(Phase 1):给每个系统预置指标补充「普通用户能看懂」的解释。

与 indicator_seed.py 分工:
  - indicator_seed.py:定义指标本身(code, params, sub_indicators)和计算依据
  - indicator_pedia.py:为每个指标补充使用者视角的信息(典型信号 / 适用场景 / 常见搭配)

设计原则(见 memory/feedback_trust_layer_valued.md):
  - 每条描述直接告诉用户「这个指标能帮我看出什么」「什么时候用」「什么时候别用」
  - 不要投资建议语气(见 memory/feedback_no_investment_advice.md),用「可能」「常被视为」描述,
    避免「应该买」「推荐卖」这类主动推荐
  - 面向散户,尽量不用专业术语;必须用时后面附一句白话解释

数据结构:
  {indicator_code: {one_liner, typical_signals, good_for, bad_for, common_pairs, sub_notes}}
"""
from __future__ import annotations

from typing import TypedDict


class TypicalSignal(TypedDict):
    """典型信号:给用户一个「看到这种情况可能意味着什么」的示例。"""
    condition: str         # 人话形式,如 "RSI12 跌破 30"
    meaning: str           # 「超卖区,可能出现反弹买点」
    caveat: str | None     # 「但单边下跌行情下会长期停留在超卖区,需配合其它指标」


class IndicatorPedia(TypedDict):
    code: str              # 指标英文 code(与 indicator_seed 对齐)
    display_name: str      # 中文名
    one_liner: str         # 一句话讲清这个指标干什么
    long_description: str  # 1-2 段详述,解释计算原理与使用思路(人话,不讲数学公式)
    typical_signals: list[TypicalSignal]
    good_for: list[str]    # 适合的市场情境
    bad_for: list[str]     # 不适合的情境(坑)
    common_pairs: list[str]  # 常见搭配(「X 指标 + Y 指标 = 双重确认」)
    sub_notes: dict[str, str]  # 每条子线的额外说明(可覆盖 indicator_seed 的简短描述)


# 20 个系统预置指标的人话字典。按 indicator_seed.py 里的顺序写,方便对照维护。
_PEDIA: dict[str, IndicatorPedia] = {
    "MA": {
        "code": "MA",
        "display_name": "移动平均线",
        "one_liner": "把最近 N 天的收盘价平均一下画出来,看价格整体在往哪走。",
        "long_description": (
            "MA 是最基础也最常用的趋势指标。把最近 N 天的收盘价加起来除以 N,就是「N 日均线」。"
            "股价在均线上方,通常说明市场在上涨趋势;在下方,通常意味着下跌趋势。"
            "多条不同周期的均线(MA5 / MA10 / MA20)同时看,还能判断趋势的强度和方向 —— "
            "短期均线在长期均线上方、且同向向上,是经典的多头排列。"
        ),
        "typical_signals": [
            {"condition": "股价站上 MA20", "meaning": "短期转强,但需要成交量配合才可靠", "caveat": "单日上穿容易假突破,观察 2-3 日收盘再确认"},
            {"condition": "MA5 上穿 MA20(金叉)", "meaning": "常被视为短期转多信号", "caveat": "震荡市里金叉死叉频繁,反而是噪音"},
            {"condition": "股价跌破 MA60 且 MA60 向下", "meaning": "中长期趋势可能转弱", "caveat": "指数级别更可信,个股受基本面影响更大"},
        ],
        "good_for": ["有明确趋势的行情(单边上涨/下跌)", "判断大方向", "设定相对客观的止损参考位"],
        "bad_for": ["震荡盘整市(均线会来回穿越,假信号多)", "成交量极小的冷门股"],
        "common_pairs": ["MACD(过滤短期噪音)", "成交量(确认突破真实性)"],
        "sub_notes": {
            "MA5": "超短线参考,几乎每天都在变,很敏感",
            "MA10": "短线关键位,常被视为「10 日生命线」",
            "MA20": "中短期趋势最重要的参考线之一",
            "MA30": "中期趋势,月线级别",
            "MA60": "中长期牛熊分界,季度级别",
        },
    },
    "KDJ": {
        "code": "KDJ",
        "display_name": "随机指标(KDJ)",
        "one_liner": "比较收盘价在最近 9 天最高最低价区间里的位置,衡量当前股价是偏高还是偏低。",
        "long_description": (
            "KDJ 由 K、D、J 三条线组成。核心思想:如果当前收盘价很接近 9 天里的最高价,说明买盘强,"
            "指标值高;反之则指标值低。J 线是 K、D 的放大版,最灵敏。"
            "通常 J 值 < 20 被视为超卖(跌多了可能反弹),J > 80 被视为超买(涨多了可能回落)。"
            "K 上穿 D(金叉)常被看作买入信号,K 下穿 D(死叉)常被看作卖出信号。"
        ),
        "typical_signals": [
            {"condition": "J 值跌破 0", "meaning": "极度超卖,反弹概率较大", "caveat": "但强势下跌中 J 可以长时间 < 0"},
            {"condition": "低位 K 上穿 D(金叉)", "meaning": "短期可能见底反弹", "caveat": "高位金叉意义弱,甚至是反转假信号"},
            {"condition": "J 值 > 100", "meaning": "极度超买,回调概率较大", "caveat": "强势上涨中同样可以长时间超买"},
        ],
        "good_for": ["震荡市里捕捉短期买卖点", "超短线交易"],
        "bad_for": ["单边强趋势(会持续钝化在超买/超卖区)", "周期过长的决策(KDJ 天然偏短)"],
        "common_pairs": ["MACD(用 MACD 定方向,用 KDJ 找时机)", "均线(用均线过滤,避开强势趋势里的假信号)"],
        "sub_notes": {
            "K": "平滑过的敏感线,兼顾灵敏度与稳定性",
            "D": "K 的均线版,更慢、更稳,信号更可靠但延迟",
            "J": "最灵敏,容易超出 0-100 区间,适合找极端",
        },
    },
    "BOLL": {
        "code": "BOLL",
        "display_name": "布林带",
        "one_liner": "以 20 日均线为中轴,上下加减 2 倍标准差形成通道,衡量股价波动范围。",
        "long_description": (
            "布林带把股价的波动范围「带」起来。正常情况下,股价 95% 的时间会运行在上下轨之间。"
            "碰到上轨常被视为短期偏强(但也可能超买),跌到下轨常被视为短期偏弱(但也可能是抄底机会)。"
            "特别值得注意的是带宽变化:带宽收窄后突然放宽(「开口」)往往预示着一轮新趋势的开始。"
        ),
        "typical_signals": [
            {"condition": "股价触及下轨并反弹", "meaning": "震荡市里常见的支撑信号", "caveat": "单边下跌市里会连续跌破下轨"},
            {"condition": "带宽明显收窄", "meaning": "波动率压缩,可能正在酝酿突破", "caveat": "突破方向要靠其它指标判断,不能只看带宽"},
            {"condition": "股价突破上轨且持续运行在上方", "meaning": "强势突破,可能开启新的趋势", "caveat": "也可能是短期超买"},
        ],
        "good_for": ["识别超买超卖", "判断波动率变化", "找突破交易时机"],
        "bad_for": ["单边强趋势(股价可能长期贴着一侧运行)"],
        "common_pairs": ["RSI(避免在超买超卖区反向操作)", "成交量(确认突破真实性)"],
        "sub_notes": {
            "UPPER": "压力参考,跌不下去时用来设止盈",
            "MID": "就是 20 日均线,趋势方向",
            "LOWER": "支撑参考,震荡市常用来做抄底参考",
        },
    },
    "MACD": {
        "code": "MACD",
        "display_name": "平滑异同移动平均(MACD)",
        "one_liner": "通过快慢两条指数均线的差,捕捉趋势的转折点和强度变化。",
        "long_description": (
            "MACD 是趋势类指标里被研究得最透的之一。DIF 线是 12 日 EMA 减 26 日 EMA — 差值越大,短期趋势越强;"
            "DEA 线是 DIF 的 9 日均线,用来过滤噪音;MACD 柱状图是 2*(DIF - DEA),柱子的伸缩直观反映动能。"
            "常见的 MACD 金叉(DIF 上穿 DEA)和死叉(DIF 下穿 DEA)是关注度最高的信号之一。"
            "零轴(DIF = 0)也是重要参考:零轴上方的金叉,常被视为更强的多头信号。"
        ),
        "typical_signals": [
            {"condition": "零轴下方金叉(DIF 上穿 DEA)", "meaning": "趋势可能正在底部转强", "caveat": "单纯金叉不够,最好配合 MACD 柱状图转红放大"},
            {"condition": "零轴上方死叉", "meaning": "上涨趋势可能转弱", "caveat": "强势市里顶部死叉也可能是中继调整"},
            {"condition": "股价创新高但 MACD 柱未创新高(顶背离)", "meaning": "上涨动能减弱,警惕顶部", "caveat": "背离信号可能反复出现几次才真正见顶"},
        ],
        "good_for": ["中长期趋势判断", "识别背离(顶/底)", "过滤震荡噪音"],
        "bad_for": ["超短线交易(MACD 本身滞后)", "流动性差的股票(数值跳跃大,信号失真)"],
        "common_pairs": ["均线(判断大方向)", "KDJ(用 KDJ 找短期时机,用 MACD 确认方向)"],
        "sub_notes": {
            "DIF": "核心差值线,反应短期趋势",
            "DEA": "DIF 的信号线,用来识别金叉/死叉",
            "MACD柱": "直观反映趋势动能,柱长越长动能越强",
        },
    },
    "EXPMA": {
        "code": "EXPMA",
        "display_name": "指数移动平均线",
        "one_liner": "给最近几天的价格更高权重的均线,比普通 MA 反应快。",
        "long_description": (
            "EXPMA 和普通 MA 一样是均线,区别在于对最近的价格权重更大。所以同样是 12 日均线,EXPMA12 比 MA12 "
            "会更快跟上最新价格变化,也就更敏感。"
            "EXPMA12 向上穿过 EXPMA26(金叉)是经典的中短期转多信号,穿过后的距离和斜率反映趋势强度。"
        ),
        "typical_signals": [
            {"condition": "EXPMA12 上穿 EXPMA26", "meaning": "中短期趋势可能转多", "caveat": "震荡市里该信号频繁,效果差"},
            {"condition": "EXPMA12 远离 EXPMA26 向上发散", "meaning": "多头趋势强化", "caveat": "发散过大后往往需要回踩确认"},
        ],
        "good_for": ["中短期趋势跟随", "对噪音相对敏感,适合短线"],
        "bad_for": ["震荡市(比 MA 还容易假信号)", "长线配置"],
        "common_pairs": ["MACD(EXPMA 本身是 MACD 的简化版)", "成交量"],
        "sub_notes": {
            "EXPMA12": "短期趋势线",
            "EXPMA26": "中期趋势线,与 EXPMA12 配合形成趋势判断",
        },
    },
    "RSI": {
        "code": "RSI",
        "display_name": "相对强弱指数",
        "one_liner": "衡量最近一段时间股票涨得多还是跌得多,判断是否超买或超卖。",
        "long_description": (
            "RSI 取值 0~100。如果最近 N 天全是上涨,RSI 接近 100(极度超买);全是下跌,接近 0(极度超卖)。"
            "经典阈值:> 70 超买、< 30 超卖。但 RSI 有个著名的「钝化」问题 —— 强势行情下 RSI 会长期停留在 70 以上,"
            "弱势行情下长期 < 30,这时「超买超卖」的结论就会失效。"
            "因此 RSI 最适合**震荡市**,在趋势市里要配合其它指标使用。"
        ),
        "typical_signals": [
            {"condition": "RSI12 跌破 30 后再次站上 30", "meaning": "超卖反弹的确认信号", "caveat": "强势下跌中可多次跌破不反弹"},
            {"condition": "股价创新高但 RSI 没创新高(顶背离)", "meaning": "上涨动能减弱", "caveat": "背离可能反复出现"},
            {"condition": "RSI > 80 且持续", "meaning": "极度超买,短期回调概率大", "caveat": "牛市中极度超买可能持续很久"},
        ],
        "good_for": ["震荡市识别超买超卖", "捕捉短期拐点", "配合背离找底部/顶部"],
        "bad_for": ["持续单边行情(易钝化)", "低流动性股票(波动剧烈时失真)"],
        "common_pairs": ["MACD(确认趋势方向,避免 RSI 钝化误判)", "均线(用来过滤震荡假信号)"],
        "sub_notes": {
            "RSI6": "6 日 RSI,最敏感,抓超短线",
            "RSI12": "12 日 RSI,最常用,平衡灵敏度与稳定性",
            "RSI24": "24 日 RSI,中期平滑,信号更可靠但延迟",
        },
    },
    "ATR": {
        "code": "ATR",
        "display_name": "真实波动幅度",
        "one_liner": "衡量股票最近平均波动多大,常用于决定止损位设多远。",
        "long_description": (
            "ATR 不告诉你方向,只告诉你「波动大小」。如果某股票 ATR14 = 2 元,说明最近 14 天平均每天波动 2 元。"
            "这对设置止损极其有用 —— 止损位距离建议至少放在 ATR 的 1.5~2 倍之外,否则容易被正常波动扫止损。"
            "ATR14_PCT(ATR 占收盘价百分比)可以跨标的比较波动率,比绝对值更有参考价值。"
        ),
        "typical_signals": [
            {"condition": "ATR 突然放大", "meaning": "波动率扩张,可能有重大方向行情", "caveat": "方向仍需其它指标判断"},
            {"condition": "ATR 持续收缩", "meaning": "波动率压缩,常预示大行情酝酿中", "caveat": "类似布林带收窄,突破方向要看其它"},
        ],
        "good_for": ["设定止损位", "跨标的比较波动率", "判断行情是「活跃」还是「死水」"],
        "bad_for": ["不适合直接做买卖决策(ATR 不给方向)"],
        "common_pairs": ["BOLL(都是波动率视角,互相印证)", "所有趋势指标(ATR 做止损工具)"],
        "sub_notes": {
            "ATR14": "绝对值,与股价单位相同",
            "ATR14_PCT": "百分比,方便跨股票比较",
        },
    },
    "WR": {
        "code": "WR",
        "display_name": "威廉指标",
        "one_liner": "判断收盘价在最近 N 日高低价区间的位置,识别超买超卖。",
        "long_description": (
            "威廉指标(Williams %R)取值 -100 ~ 0。-20 以上通常被视为超买,-80 以下为超卖。"
            "它和 KDJ 的 K 值逻辑很像,但方向相反(WR 越接近 0 表示价格越接近高点)。"
            "对超短线交易者友好,反应灵敏。"
        ),
        "typical_signals": [
            {"condition": "WR6 跌破 -80 后回升", "meaning": "超短期超卖反弹", "caveat": "震荡市有效,趋势市要谨慎"},
            {"condition": "WR6 持续在 -20 上方", "meaning": "强势状态,可能延续", "caveat": "极度超买也意味着风险累积"},
        ],
        "good_for": ["超短线交易", "震荡市捕捉拐点"],
        "bad_for": ["持续单边趋势", "需要与趋势指标配合使用"],
        "common_pairs": ["KDJ(两者互相印证)", "均线(判断大方向)"],
        "sub_notes": {
            "WR10": "10 日版本,较稳定",
            "WR6": "6 日版本,更灵敏,适合更短的周期",
        },
    },
    "STOCK_DATA": {
        "code": "STOCK_DATA",
        "display_name": "个股原始行情",
        "one_liner": "个股最基础的 OHLC 价格和成交量,可直接作为条件使用。",
        "long_description": (
            "不是传统意义的「指标」,而是把股价、成交量、换手率等原始字段以指标形式暴露出来,"
            "方便在 DSL 表达式里作为条件直接使用。"
            "比如「成交量 > 昨日 2 倍」「换手率 > 5%」这类条件,就要引用 STOCK_DATA 的子线。"
        ),
        "typical_signals": [
            {"condition": "成交量 > 前 20 日均量 2 倍", "meaning": "放量信号,常伴随趋势启动或反转", "caveat": "放量不一定是好事,也可能是高位出货"},
            {"condition": "换手率 > 5%", "meaning": "活跃度高,资金进出明显", "caveat": "低市值股票换手率天然高"},
        ],
        "good_for": ["组合条件的基础数据源", "定义自己的原创指标"],
        "bad_for": ["不能单独作为买卖依据"],
        "common_pairs": ["所有其它指标"],
        "sub_notes": {
            "close": "收盘价,最常用作价格基准",
            "open": "开盘价,反映开盘多空博弈",
            "high": "最高价,当日压力测试",
            "low": "最低价,当日支撑测试",
            "turnover_rate": "换手率(%),反映交投活跃度",
            "volume": "成交量(手),判断资金参与度",
        },
    },
    "CCI": {
        "code": "CCI",
        "display_name": "商品通道指数",
        "one_liner": "衡量价格偏离均值的程度,识别极端行情后的反转机会。",
        "long_description": (
            "CCI 原本是为商品期货设计的,后来被广泛用于股票。取值范围理论无界,但大部分时间在 -300 ~ +300 之间。"
            "> +100 被视为超买区,< -100 为超卖区。"
            "CCI 的优势是对**极端偏离**敏感,常用来抓反转拐点。"
        ),
        "typical_signals": [
            {"condition": "CCI 跌破 -100 后回升穿过 -100", "meaning": "超卖反弹的经典信号", "caveat": "单边下跌中可多次跌破"},
            {"condition": "CCI 冲高回落跌破 +100", "meaning": "超买回落信号", "caveat": "强势市里该信号意义减弱"},
        ],
        "good_for": ["识别极端偏离后的反转", "震荡市"],
        "bad_for": ["趋势初期(CCI 容易过早给反转信号)"],
        "common_pairs": ["均线(确认大方向)", "MACD"],
        "sub_notes": {
            "CCI14": "标准参数 14 日 CCI,取值常在 -300~+300 之间",
        },
    },
    "BIAS": {
        "code": "BIAS",
        "display_name": "乖离率",
        "one_liner": "衡量股价相对于移动均线偏离了多少,判断是否涨过头或跌过头。",
        "long_description": (
            "BIAS = (收盘价 - N 日均线) / N 日均线 × 100%,直接以百分比表示股价偏离均线的程度。"
            "正值表示股价在均线上方(可能偏热),负值表示在均线下方(可能偏冷)。"
            "不同周期 BIAS 的阈值不同 —— 一般 6 日 BIAS > +6% 或 < -6% 就要警惕,12 日 BIAS 的极值区间更宽。"
        ),
        "typical_signals": [
            {"condition": "BIAS6 < -8%", "meaning": "短期超卖,可能乖离回归(反弹)", "caveat": "强势下跌可持续负乖离"},
            {"condition": "BIAS6 > +8%", "meaning": "短期超买,可能乖离回归(回落)", "caveat": "强势上涨可持续正乖离"},
        ],
        "good_for": ["捕捉短期乖离过大的机会", "震荡市回归交易"],
        "bad_for": ["趋势初期(乖离扩大后还会继续扩大)"],
        "common_pairs": ["均线(BIAS 就是围绕均线设计的)", "RSI"],
        "sub_notes": {
            "BIAS6": "6 日乖离率,短期超买超卖",
            "BIAS12": "12 日乖离率,中期参考",
            "BIAS24": "24 日乖离率,较平滑,中期偏长",
        },
    },
    "ROC": {
        "code": "ROC",
        "display_name": "变化率指标",
        "one_liner": "当前价相对 N 日前价格的涨跌幅,衡量动能。",
        "long_description": (
            "ROC = (当前收盘价 / N 日前收盘价 - 1) × 100%。简单直接 —— 正值表示涨,负值表示跌,"
            "绝对值大小反映动能强弱。"
            "ROC 上穿零轴常被视为转多信号,下穿零轴为转空。"
        ),
        "typical_signals": [
            {"condition": "ROC 由负转正(穿越零轴)", "meaning": "短期动能由弱转强", "caveat": "震荡市中频繁穿越,信号弱"},
            {"condition": "ROC 创新高", "meaning": "动能强化", "caveat": "高位背离需警惕"},
        ],
        "good_for": ["衡量趋势动能", "配合其它指标确认方向"],
        "bad_for": ["震荡市(零轴附近来回摆动)"],
        "common_pairs": ["MACD(都是动能类)", "均线"],
        "sub_notes": {
            "ROC6": "6 日变化率,灵敏度较高",
            "ROC12": "12 日变化率,较为平滑",
        },
    },
    "PSY": {
        "code": "PSY",
        "display_name": "心理线",
        "one_liner": "统计最近 N 天里上涨的天数占比,反映多空情绪。",
        "long_description": (
            "PSY 非常朴素:最近 12 天里涨了几天,除以 12 再乘 100。如果 12 天全涨,PSY = 100。"
            "75 以上被视为超买区(连涨太多,可能要歇歇),25 以下为超卖区(连跌太多,可能反弹)。"
            "PSY 更多是市场情绪的温度计,不直接给精确买卖点。"
        ),
        "typical_signals": [
            {"condition": "PSY > 75", "meaning": "多头情绪过热,短期可能回调", "caveat": "强势市可持续高位"},
            {"condition": "PSY < 25", "meaning": "空头情绪过度,短期可能反弹", "caveat": "熊市可持续低位"},
        ],
        "good_for": ["情绪极端值识别", "中短期超买超卖"],
        "bad_for": ["精确买卖时机", "趋势强的行情"],
        "common_pairs": ["RSI、KDJ 等其它情绪指标", "均线(方向确认)"],
        "sub_notes": {
            "PSY12": "12 日心理线(%),取值 0~100",
        },
    },
    "VOLS": {
        "code": "VOLS",
        "display_name": "量能均线",
        "one_liner": "把成交量做平均,判断最近是放量还是缩量。",
        "long_description": (
            "VOLS 就是「成交量的均线」。交易员常说的「放量」「缩量」,标准通常是和成交量均线比较。"
            "当日成交量突破 VMA5(5 日量能均线)并持续,是放量信号;持续低于 VMA5 则是缩量。"
            "放量上涨通常比缩量上涨更可靠(资金参与多);放量下跌常是出货信号。"
        ),
        "typical_signals": [
            {"condition": "当日成交量 > VMA5 * 2", "meaning": "显著放量,可能有重要消息或主力动作", "caveat": "放量不一定是好事,看方向"},
            {"condition": "连续多日成交量 < VMA20", "meaning": "持续缩量,市场观望", "caveat": "缩量见底常见于下跌末期"},
        ],
        "good_for": ["判断突破真实性", "识别主力资金进出"],
        "bad_for": ["判断方向(VOLS 只说「有没有量」,不说方向)"],
        "common_pairs": ["所有趋势指标(用量能确认信号真实性)"],
        "sub_notes": {
            "VMA5": "5 日量能均线,短期放量/缩量基准",
            "VMA10": "10 日量能均线",
            "VMA20": "20 日量能均线,重要的量能参考",
        },
    },
    "OBV": {
        "code": "OBV",
        "display_name": "能量潮",
        "one_liner": "用累计成交量方向来衡量资金流入流出。",
        "long_description": (
            "OBV 的逻辑:上涨日就加上当日成交量,下跌日就减去。如果 OBV 持续上升,说明整体资金在流入;"
            "持续下跌说明资金在流出。"
            "OBV 的绝对值意义不大,更看其走势与股价是否一致 —— 股价创新高但 OBV 没创新高,常被视为顶背离。"
        ),
        "typical_signals": [
            {"condition": "OBV 持续上升但股价滞涨", "meaning": "资金在悄悄流入,可能蓄势", "caveat": "需要其它指标确认"},
            {"condition": "股价创新高但 OBV 未创新高(顶背离)", "meaning": "警示上涨动能不足", "caveat": "背离可能持续很久才验证"},
        ],
        "good_for": ["识别资金流向", "配合股价做背离分析"],
        "bad_for": ["精确买卖时机(OBV 趋势型指标)"],
        "common_pairs": ["价格走势", "MACD"],
        "sub_notes": {
            "OBV": "累计能量潮(单位:手),用于做顶底背离分析",
        },
    },
    "DMA": {
        "code": "DMA",
        "display_name": "平行线差",
        "one_liner": "短期均线与中期均线的差,衡量短线相对中线的强弱。",
        "long_description": (
            "DMA = MA10 - MA20。正值表示短线在中线上方(短线强),负值反之。"
            "DMA 本身和它的 10 日均线 DDMA 构成一对信号 —— DMA 上穿 DDMA 常视为买入信号,反之为卖出。"
            "相比纯均线金叉,DMA 金叉更平滑、假信号稍少。"
        ),
        "typical_signals": [
            {"condition": "DMA 由负转正", "meaning": "短线转强于中线,趋势可能转多", "caveat": "震荡市里意义不大"},
            {"condition": "DMA 上穿 DDMA", "meaning": "买入信号(比单纯 MA 金叉更平滑)", "caveat": "仍需配合其它指标"},
        ],
        "good_for": ["中短期趋势捕捉", "比 MA 金叉更稳定"],
        "bad_for": ["震荡市"],
        "common_pairs": ["MACD、EXPMA 等其它趋势指标"],
        "sub_notes": {
            "DMA": "MA10 与 MA20 的差值,正值短线偏强",
            "DDMA": "DMA 的 10 日均线,作为信号线",
        },
    },
    "TRIX": {
        "code": "TRIX",
        "display_name": "三重指数平滑",
        "one_liner": "对价格做三次指数平滑,过滤噪音,看中长期趋势的动能。",
        "long_description": (
            "TRIX 经过三次 EMA 平滑,对短期波动几乎免疫,只保留趋势级别的变化。"
            "TRIX > 0 表示趋势向上,TRIX < 0 表示趋势向下。TRIX 上穿 TRMA(信号线)是经典金叉。"
            "因为平滑度高,TRIX 的信号比较迟,但假信号也少,适合中长期操作。"
        ),
        "typical_signals": [
            {"condition": "TRIX 上穿零轴", "meaning": "中期趋势可能转多", "caveat": "信号迟缓,可能错过前段涨幅"},
            {"condition": "TRIX 上穿 TRMA(金叉)", "meaning": "经典买入信号", "caveat": "类似 MACD 金叉,震荡市意义弱"},
        ],
        "good_for": ["中长期趋势跟随", "过滤短期噪音"],
        "bad_for": ["短线交易(信号太迟)", "震荡市"],
        "common_pairs": ["MACD(都是平滑的趋势类)", "均线"],
        "sub_notes": {
            "TRIX12": "三重 EMA 的日变化率(%),零轴上方多头",
            "TRMA": "TRIX 的 9 日均线(信号线)",
        },
    },
    "DMI": {
        "code": "DMI",
        "display_name": "趋向指标(DMI)",
        "one_liner": "通过 +DI / -DI / ADX 三条线判断当前是趋势市还是震荡市,以及趋势有多强。",
        "long_description": (
            "DMI 由三条线组成:+DI 代表多头力量,-DI 代表空头力量,ADX 代表趋势强度(不管方向)。"
            "核心用法:ADX > 25 说明有明显趋势(方向看 +DI 和 -DI 哪个在上面);ADX < 20 说明市场在震荡,这时方向类指标往往不可靠。"
            "所以 DMI 不是用来直接买卖的,而是用来「决定用不用其它指标」的前置过滤器。"
        ),
        "typical_signals": [
            {"condition": "ADX > 25 且 +DI > -DI", "meaning": "多头趋势明确", "caveat": "ADX 高只表示趋势强,具体方向看 DI"},
            {"condition": "ADX < 20", "meaning": "无明显趋势,震荡市", "caveat": "此时趋势类指标(如 MACD 金叉)信号不可靠"},
            {"condition": "+DI 上穿 -DI 且 ADX 同步上升", "meaning": "新的多头趋势可能开始", "caveat": "仍需价格走势确认"},
        ],
        "good_for": ["判断市场处于趋势还是震荡", "筛选使用其它指标的前提", "识别趋势起始"],
        "bad_for": ["单独做买卖决策", "短线捕捉"],
        "common_pairs": ["几乎所有趋势指标(DMI 做状态过滤器)"],
        "sub_notes": {
            "PDI": "+DI,多头力量",
            "MDI": "-DI,空头力量",
            "ADX": "趋势强度,不分方向",
        },
    },
    "STDDEV": {
        "code": "STDDEV",
        "display_name": "价格标准差",
        "one_liner": "衡量一段时间内价格波动的离散程度,判断波动率高低。",
        "long_description": (
            "STDDEV 是统计学的标准差,应用在股价上就是衡量「价格有多不稳」。"
            "数值大表示价格波动大(风险高,但机会也多),数值小表示价格相对平稳。"
            "STDDEV 从低位开始上升,常预示着行情即将启动。"
        ),
        "typical_signals": [
            {"condition": "STDDEV 持续压缩到极低", "meaning": "波动率极度压缩,大行情酝酿中", "caveat": "方向未知"},
            {"condition": "STDDEV 突然放大", "meaning": "波动率急剧放大,可能有重要行情", "caveat": "需其它指标判断方向"},
        ],
        "good_for": ["识别波动率变化", "突破交易的前置过滤"],
        "bad_for": ["单独判断方向"],
        "common_pairs": ["布林带(底层原理类似)", "ATR"],
        "sub_notes": {
            "STDDEV10": "10 日价格标准差",
            "STDDEV20": "20 日价格标准差",
        },
    },
    "ARBR": {
        "code": "ARBR",
        "display_name": "人气意愿指标",
        "one_liner": "AR 衡量一天内买卖双方的意愿,BR 衡量相对前收盘的买卖力量对比。",
        "long_description": (
            "AR(人气指标)比较当日最高价、最低价与开盘价的距离,反映当日买卖意愿。"
            "BR(意愿指标)比较当日最高价、最低价与前收盘价的距离,反映多空力量对比。"
            "AR 正常区间 50~150,BR 更容易走极端。BR > AR 通常意味着多头主动性更强。"
            "这对指标的理解门槛较高,新手不建议单独使用。"
        ),
        "typical_signals": [
            {"condition": "AR > 150 且 BR > 200", "meaning": "多头气氛过热,回调风险大", "caveat": "强势市可持续高位"},
            {"condition": "AR < 50 且 BR < 50", "meaning": "多头气氛低迷,可能接近底部", "caveat": "熊市末期可更低"},
        ],
        "good_for": ["识别市场情绪极端值"],
        "bad_for": ["新手直接使用", "短线交易"],
        "common_pairs": ["PSY、RSI 等其它情绪指标"],
        "sub_notes": {
            "AR": "人气指标,正常区间 50~150",
            "BR": "意愿指标,BR > AR 为多头强势",
        },
    },
}


def get_indicator_pedia(code: str) -> IndicatorPedia | None:
    """按 indicator.code(如 'RSI')取人话百科。未收录的自定义指标返回 None。"""
    return _PEDIA.get(code.upper())


def list_indicator_pedia() -> list[IndicatorPedia]:
    """返回所有已收录指标的人话百科列表(按 indicator_seed.py 的顺序)。"""
    return list(_PEDIA.values())
