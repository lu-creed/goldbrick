/**
 * 回测结果的「人话总结 + 四维星级」工具。
 *
 * 设计原则:
 *   - 不用 LLM。所有文案靠 if-else + 模板字符串,规则明确可预期。
 *   - 人话要避免投资建议语气(见 memory/feedback_no_investment_advice.md)。
 *     不说「应该买」「推荐」,说「回测显示」「过去的数据表明」等描述性语气。
 *   - 四维星级用硬阈值,不是主观打分。用户能推出每颗星是怎么来的。
 *
 * 四个维度:
 *   - profitability  (收益性):综合总收益和超越基准的幅度
 *   - risk_control   (风险控制):主要看最大回撤和夏普比率
 *   - stability      (稳定性):看胜率和盈亏比
 *   - trade_frequency(交易频率):看月均交易次数,太高太低都扣分
 */
import type { BacktestRunOut } from "../api/client";


export interface BacktestStars {
  profitability: number;     // 收益性 1-5
  risk_control: number;       // 风险控制 1-5
  stability: number;          // 稳定性 1-5
  trade_frequency: number;    // 交易频率 1-5
}


/**
 * 根据回测结果生成一段人话总结。
 *
 * 例:「过去 3 年,这个策略共交易 47 次,平均每月 1.3 次。
 *      总收益 +23.5%,同期基准 +8.2%,跑赢 15.3 个百分点。
 *      最大回撤 -12.0%,风险可控。胜率 52%,算略高于随机水平。」
 */
export function generateNarrative(result: BacktestRunOut): string {
  const parts: string[] = [];

  // 1. 时间范围与交易频率
  const days = daysBetween(result.start_date, result.end_date);
  const months = Math.max(1, days / 30);
  const years = days / 365;
  const period = years >= 1
    ? `${years.toFixed(1)} 年`
    : `${Math.round(months)} 个月`;
  const tradesPerMonth = result.total_trades / months;

  if (result.total_trades === 0) {
    parts.push(
      `过去 ${period} 里,这个策略在当前参数下**没有触发任何交易**。` +
      `可能是阈值过于严格,或者选定时间内市场没有出现满足条件的行情。建议放宽阈值或换一段时间再试。`,
    );
    return parts.join("\n\n");
  }

  parts.push(
    `过去 ${period},这个策略共交易 ${result.total_trades} 次,` +
    `平均每月 ${tradesPerMonth.toFixed(1)} 次。`,
  );

  // 2. 收益 vs 基准
  const retSign = result.total_return_pct >= 0 ? "+" : "";
  let returnLine = `总收益 ${retSign}${result.total_return_pct.toFixed(1)}%`;
  if (result.benchmark_return_pct != null) {
    const benchSign = result.benchmark_return_pct >= 0 ? "+" : "";
    const alpha = result.alpha_pct ?? (result.total_return_pct - result.benchmark_return_pct);
    returnLine += `,同期基准 ${benchSign}${result.benchmark_return_pct.toFixed(1)}%,` +
      `${alpha >= 0 ? "跑赢" : "跑输"}基准 ${Math.abs(alpha).toFixed(1)} 个百分点。`;
  } else {
    returnLine += "(本次未选基准对照)。";
  }
  parts.push(returnLine);

  // 3. 风险与稳定性
  const riskParts: string[] = [];
  const dd = Math.abs(result.max_drawdown_pct);
  if (dd < 10) {
    riskParts.push(`最大回撤 -${dd.toFixed(1)}%,风险控制较好`);
  } else if (dd < 20) {
    riskParts.push(`最大回撤 -${dd.toFixed(1)}%,风险可控`);
  } else if (dd < 30) {
    riskParts.push(`最大回撤 -${dd.toFixed(1)}%,波动较大`);
  } else {
    riskParts.push(`最大回撤 -${dd.toFixed(1)}%,下行风险明显`);
  }

  if (result.win_rate != null) {
    if (result.win_rate >= 60) {
      riskParts.push(`胜率 ${result.win_rate.toFixed(0)}%,相对稳健`);
    } else if (result.win_rate >= 50) {
      riskParts.push(`胜率 ${result.win_rate.toFixed(0)}%,略高于随机`);
    } else if (result.win_rate >= 40) {
      riskParts.push(`胜率 ${result.win_rate.toFixed(0)}%,低于半数但若盈亏比足够仍可能盈利`);
    } else {
      riskParts.push(`胜率仅 ${result.win_rate.toFixed(0)}%,需检查是否过度交易`);
    }
  }

  if (result.sharpe_ratio != null) {
    if (result.sharpe_ratio >= 1.5) {
      riskParts.push(`夏普比率 ${result.sharpe_ratio.toFixed(2)},收益/风险关系优秀`);
    } else if (result.sharpe_ratio >= 1.0) {
      riskParts.push(`夏普比率 ${result.sharpe_ratio.toFixed(2)},收益/风险较平衡`);
    } else if (result.sharpe_ratio >= 0.5) {
      riskParts.push(`夏普比率 ${result.sharpe_ratio.toFixed(2)},有效但收益对波动补偿不高`);
    } else {
      riskParts.push(`夏普比率仅 ${result.sharpe_ratio.toFixed(2)},承担的风险未获得理想回报`);
    }
  }
  if (riskParts.length > 0) {
    parts.push(riskParts.join(";") + "。");
  }

  // 4. 结尾(描述性,非建议)
  if (result.total_return_pct > 20 && dd < 15) {
    parts.push("⚡ 历史回测表现突出,但请注意:过去表现不代表未来,建议用参数敏感性扫描进一步验证策略鲁棒性。");
  } else if (result.total_return_pct < 0) {
    parts.push("⚠️ 这段时间里该策略整体亏损,请检查指标选择、阈值设定或时间窗口是否合适。");
  }

  return parts.join("\n\n");
}


/**
 * 根据回测结果计算四维星级(各 1-5 星)。
 *
 * 所有星级都走硬阈值,不做主观打分。规则在函数内注释里透明列出,
 * 便于用户理解「为什么只给了 3 星」。
 */
export function computeStars(result: BacktestRunOut): BacktestStars {
  const start = new Date(result.start_date);
  const end = new Date(result.end_date);
  const months = Math.max(1, (end.getTime() - start.getTime()) / (30 * 86400000));

  // ── 收益性:看总收益 vs 基准,其次看绝对收益率 ───────────────
  // 5 星:跑赢基准 20pp 以上 或 无基准时绝对收益 > 30%
  // 4 星:跑赢基准 10pp 或 绝对收益 > 20%
  // 3 星:跑赢基准 或 绝对收益为正
  // 2 星:跑输基准但正收益 或 小幅负收益
  // 1 星:明显亏损
  let profitability = 1;
  const totalRet = result.total_return_pct;
  const bench = result.benchmark_return_pct;
  if (bench != null) {
    const alpha = (result.alpha_pct ?? (totalRet - bench));
    if (alpha >= 20) profitability = 5;
    else if (alpha >= 10) profitability = 4;
    else if (alpha >= 0) profitability = 3;
    else if (totalRet >= 0) profitability = 2;
    else profitability = 1;
  } else {
    if (totalRet >= 30) profitability = 5;
    else if (totalRet >= 20) profitability = 4;
    else if (totalRet >= 5) profitability = 3;
    else if (totalRet >= 0) profitability = 2;
    else profitability = 1;
  }

  // ── 风险控制:主要看最大回撤 ───────────────
  // 5 星:回撤 < 8%
  // 4 星:回撤 8%-15%
  // 3 星:回撤 15%-20%
  // 2 星:回撤 20%-30%
  // 1 星:回撤 > 30%
  let risk_control: number;
  const dd = Math.abs(result.max_drawdown_pct);
  if (dd < 8) risk_control = 5;
  else if (dd < 15) risk_control = 4;
  else if (dd < 20) risk_control = 3;
  else if (dd < 30) risk_control = 2;
  else risk_control = 1;

  // ── 稳定性:胜率 × 夏普 综合 ───────────────
  // 用两个字段的平均分(各 0-5),再 round。
  let stability_raw = 3;  // 无数据时给 3(不褒不贬)
  let stability_samples = 0;
  let stability_sum = 0;
  if (result.win_rate != null) {
    stability_samples += 1;
    if (result.win_rate >= 60) stability_sum += 5;
    else if (result.win_rate >= 55) stability_sum += 4;
    else if (result.win_rate >= 50) stability_sum += 3;
    else if (result.win_rate >= 40) stability_sum += 2;
    else stability_sum += 1;
  }
  if (result.sharpe_ratio != null) {
    stability_samples += 1;
    if (result.sharpe_ratio >= 1.5) stability_sum += 5;
    else if (result.sharpe_ratio >= 1.0) stability_sum += 4;
    else if (result.sharpe_ratio >= 0.5) stability_sum += 3;
    else if (result.sharpe_ratio >= 0) stability_sum += 2;
    else stability_sum += 1;
  }
  if (stability_samples > 0) {
    stability_raw = stability_sum / stability_samples;
  }
  const stability = Math.max(1, Math.min(5, Math.round(stability_raw)));

  // ── 交易频率:月均交易次数,2-5 次 5 星,太高太低扣 ───────────────
  // 月均 2-5 次:5 星(节奏舒服)
  // 月均 1-2 或 5-8 次:4 星
  // 月均 0.5-1 或 8-15 次:3 星
  // 月均 < 0.5(太少):2 星
  // 月均 > 15(过频):1 星
  let trade_frequency: number;
  const per_month = result.total_trades / months;
  if (per_month >= 2 && per_month <= 5) trade_frequency = 5;
  else if ((per_month >= 1 && per_month < 2) || (per_month > 5 && per_month <= 8)) trade_frequency = 4;
  else if ((per_month >= 0.5 && per_month < 1) || (per_month > 8 && per_month <= 15)) trade_frequency = 3;
  else if (per_month < 0.5) trade_frequency = 2;
  else trade_frequency = 1;

  return { profitability, risk_control, stability, trade_frequency };
}


/** 算两个 YYYY-MM-DD 字符串之间的天数。不做时区转换,按日历天算。 */
function daysBetween(startDate: string, endDate: string): number {
  const s = new Date(startDate);
  const e = new Date(endDate);
  return Math.max(1, Math.round((e.getTime() - s.getTime()) / 86400000));
}
