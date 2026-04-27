/**
 * 条件选股回测页面
 *
 * 功能：基于 DSL 自定义指标对全市场逐日扫描，满足买入条件建仓、满足卖出条件平仓。
 * 等额分配资金，最多同时持有指定只数。
 *
 * 页面结构：
 * - 配置区：回测参数表单
 * - 绩效总览：收益率 / 年化 / 夏普 / 最大回撤 / 卡玛 / 盈亏比 等核心指标
 * - 交易分析：胜率 / 平均持仓天数 / 平均盈亏幅度
 * - 资金曲线：双轴折线（总权益 + 回撤%）
 * - 交易记录：分页明细表
 */
import * as echarts from "echarts";
import {
  Button,
  Card,
  Col,
  DatePicker,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  AppstoreOutlined,
  InfoCircleOutlined,
  LineChartOutlined,
  RiseOutlined,
  FallOutlined,
  SwapOutlined,
  TrophyOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  fetchCustomIndicators,
  fetchTradeChart,
  getApiErrorMessage,
  runBacktest,
  type BacktestRunOut,
  type BacktestTradeRow,
  type TradeChartOut,
  type UserIndicatorOut,
} from "../api/client";
import { ECHARTS_BASE_OPTION, FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";
import { useIsMobile } from "../hooks/useIsMobile";

const { Title, Text, Paragraph } = Typography;

/** 比较运算符选项 */
const compareOptions = [
  { value: "gt",  label: "大于 >" },
  { value: "gte", label: "大于等于 ≥" },
  { value: "lt",  label: "小于 <" },
  { value: "le",  label: "小于等于 ≤" },
  { value: "eq",  label: "等于 =" },
  { value: "ne",  label: "不等于 ≠" },
];

const OP_SYMBOL: Record<string, string> = {
  gt: ">", gte: "≥", lt: "<", le: "≤", eq: "=", ne: "≠",
};

/** 从 DSL 指标定义中提取可参与选股/回测的子线选项 */
function screeningSubKeys(def: Record<string, unknown> | null | undefined) {
  const subs = def?.sub_indicators as
    | { key?: string; name?: string; use_in_screening?: boolean; auxiliary_only?: boolean }[]
    | undefined;
  if (!Array.isArray(subs)) return [];
  return subs
    .filter((s) => s.key && s.use_in_screening !== false && !s.auxiliary_only)
    .map((s) => ({ value: s.key!, label: `${s.name || s.key} (${s.key})` }));
}

/** 格式化金额（千分符 + 2位小数） */
function fmtMoney(v: number) {
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 带符号的百分比格式化 */
function fmtPct(v: number | null | undefined, precision = 2): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(precision)}%`;
}

/** 数值的涨跌颜色 */
function pnlColor(v: number | null | undefined) {
  if (v == null) return undefined;
  return v > 0 ? RISE_COLOR : v < 0 ? FALL_COLOR : FLAT_COLOR;
}

/** 单个绩效指标卡片（带 tooltip 说明） */
function MetricCard({
  title,
  value,
  suffix = "",
  hint,
  color,
  precision = 2,
}: {
  title: string;
  value: number | null | undefined;
  suffix?: string;
  hint?: string;
  color?: string;
  precision?: number;
}) {
  const label = (
    <Space size={4}>
      {title}
      {hint && (
        <Tooltip title={hint}>
          <InfoCircleOutlined style={{ fontSize: 11, color: "#8c8c8c" }} />
        </Tooltip>
      )}
    </Space>
  );
  return (
    <Statistic
      title={label}
      value={value == null ? "—" : Number(value.toFixed(precision))}
      suffix={value == null ? "" : suffix}
      valueStyle={{ color: color ?? (value == null ? "#8c8c8c" : undefined), fontSize: 20 }}
      precision={value == null ? 0 : precision}
    />
  );
}

// ── 策略模板 ──────────────────────────────────────────────────────────────────

interface StrategyTemplate {
  key: string;
  name: string;
  badge: string;
  desc: string;
  indicatorCode: string;   // 对应预置指标的 code（tpl_*）
  subKey: string;          // 对应预置指标的子线 key
  buy_op: string;
  buy_threshold: number;
  sell_op: string;
  sell_threshold: number;
  max_positions: number;
}

const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  {
    key: "rsi_oversold",
    name: "RSI 超卖反弹",
    badge: "震荡适用",
    desc: "RSI12 跌破 30（超卖区）时买入，升回 70 以上（超买区）时卖出。适合宽幅震荡行情，追求高胜率的短线交易。",
    indicatorCode: "tpl_rsi",
    subKey: "rsi12",
    buy_op: "lt",  buy_threshold: 30,
    sell_op: "gt", sell_threshold: 70,
    max_positions: 3,
  },
  {
    key: "ma_cross",
    name: "MA 均线金叉",
    badge: "趋势跟随",
    desc: "MA5-MA20 差值由负转正（金叉）时买入，由正转负（死叉）时卖出。适合趋势行情，持仓周期较长。",
    indicatorCode: "tpl_ma_cross",
    subKey: "diff",
    buy_op: "gt",  buy_threshold: 0,
    sell_op: "lt", sell_threshold: 0,
    max_positions: 5,
  },
  {
    key: "macd_signal",
    name: "MACD 金叉",
    badge: "动量信号",
    desc: "MACD 柱状线由负转正（多头动能）时买入，由正转负时卖出。信号相对 MA 滞后，适合中线持仓。",
    indicatorCode: "tpl_macd_bar",
    subKey: "bar",
    buy_op: "gt",  buy_threshold: 0,
    sell_op: "lt", sell_threshold: 0,
    max_positions: 3,
  },
  {
    key: "boll_rebound",
    name: "布林下轨反弹",
    badge: "均值回归",
    desc: "价格在布林带中的位置（0=下轨，1=上轨）低于 0.1 时买入（触及下轨超卖），高于 0.5 时卖出（收复中轨）。",
    indicatorCode: "tpl_boll_pos",
    subKey: "pos",
    buy_op: "lt",  buy_threshold: 0.1,
    sell_op: "gt", sell_threshold: 0.5,
    max_positions: 5,
  },
  {
    key: "kdj_oversold",
    name: "KDJ 超卖",
    badge: "短线反弹",
    desc: "KDJ 的 J 值低于 20（深度超卖）时买入，高于 80（超买）时卖出。J 值波动剧烈，信号频繁，适合短线。",
    indicatorCode: "tpl_kdj_j",
    subKey: "j",
    buy_op: "lt",  buy_threshold: 20,
    sell_op: "gt", sell_threshold: 80,
    max_positions: 3,
  },
  {
    key: "cci_oversold",
    name: "CCI 超卖反弹",
    badge: "震荡适用",
    desc: "CCI14 低于 -100（极端超卖）时买入，高于 +100（超买）时卖出。CCI 对急跌反弹捕捉灵敏，适合震荡市。",
    indicatorCode: "tpl_cci",
    subKey: "cci14",
    buy_op: "lt",  buy_threshold: -100,
    sell_op: "gt", sell_threshold: 100,
    max_positions: 3,
  },
  {
    key: "bias_oversold",
    name: "BIAS 乖离回归",
    badge: "均值回归",
    desc: "BIAS12 低于 -8%（价格明显低于12日均线）时买入，高于 +5% 时卖出。适合均值回归策略，规避单边下跌行情。",
    indicatorCode: "tpl_bias",
    subKey: "bias12",
    buy_op: "lt",  buy_threshold: -8,
    sell_op: "gt", sell_threshold: 5,
    max_positions: 5,
  },
  {
    key: "roc_momentum",
    name: "ROC 动量突破",
    badge: "动量信号",
    desc: "ROC12 高于 +5%（强势上涨动量）时买入顺势，低于 -5% 时止损离场。适合趋势明显的单边行情。",
    indicatorCode: "tpl_roc",
    subKey: "roc12",
    buy_op: "gt",  buy_threshold: 5,
    sell_op: "lt", sell_threshold: -5,
    max_positions: 3,
  },
  {
    key: "vol_ratio",
    name: "量比放量突破",
    badge: "量价配合",
    desc: "成交量/VMA20 量比高于 2（当日放量超过均量2倍）时买入，低于 0.5（明显缩量）时卖出。",
    indicatorCode: "tpl_vol_ratio",
    subKey: "vol_ratio",
    buy_op: "gt",  buy_threshold: 2,
    sell_op: "lt", sell_threshold: 0.5,
    max_positions: 5,
  },
  {
    key: "trix_cross",
    name: "TRIX 零轴金叉",
    badge: "趋势跟随",
    desc: "TRIX12 从下方上穿 0 轴时买入（趋势转多），从上方下穿 0 轴时卖出。TRIX 经过三次平滑，可过滤大量噪音。",
    indicatorCode: "tpl_trix",
    subKey: "trix12",
    buy_op: "gt",  buy_threshold: 0,
    sell_op: "lt", sell_threshold: 0,
    max_positions: 5,
  },
];

const BADGE_COLOR: Record<string, string> = {
  "震荡适用": "purple",
  "趋势跟随": "blue",
  "动量信号": "cyan",
  "均值回归": "geekblue",
  "短线反弹": "orange",
  "量价配合": "green",
};

interface TemplatePanelProps {
  open: boolean;
  onClose: () => void;
  onApply: (tpl: StrategyTemplate) => void;
}

function TemplatePanel({ open, onClose, onApply }: TemplatePanelProps) {
  return (
    <Modal
      title="策略模板"
      open={open}
      onCancel={onClose}
      footer={null}
      width={Math.min(700, window.innerWidth * 0.95)}
    >
      <div style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          点击「使用模板」后，对应指标和买卖条件会自动填入表单，直接点「开始回测」即可。
        </Typography.Text>
      </div>
      <Space direction="vertical" style={{ width: "100%" }} size={10}>
        {STRATEGY_TEMPLATES.map((tpl) => (
          <Card
            key={tpl.key}
            size="small"
            styles={{ body: { padding: "10px 14px" } }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <Space size={8} style={{ marginBottom: 4 }}>
                  <Typography.Text strong>{tpl.name}</Typography.Text>
                  <Tag color={BADGE_COLOR[tpl.badge] ?? "default"} style={{ fontSize: 11 }}>{tpl.badge}</Tag>
                </Space>
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: "0 0 6px" }}>
                  {tpl.desc}
                </Typography.Paragraph>
                <Space size={16}>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    买入：子线 {tpl.buy_op === "lt" ? "<" : tpl.buy_op === "gt" ? ">" : tpl.buy_op === "lte" ? "≤" : "≥"} {tpl.buy_threshold}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    卖出：子线 {tpl.sell_op === "lt" ? "<" : tpl.sell_op === "gt" ? ">" : tpl.sell_op === "lte" ? "≤" : "≥"} {tpl.sell_threshold}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    最大持仓：{tpl.max_positions} 只
                  </Typography.Text>
                </Space>
              </div>
              <Button
                type="primary"
                size="small"
                style={{ marginLeft: 16, flexShrink: 0 }}
                onClick={() => { onApply(tpl); onClose(); }}
              >
                使用模板
              </Button>
            </div>
          </Card>
        ))}
      </Space>
    </Modal>
  );
}

// ── 交易验证 Drawer ───────────────────────────────────────────────────────────

interface TradeDetailDrawerProps {
  open: boolean;
  onClose: () => void;
  onAfterClose?: () => void;
  trade: BacktestTradeRow | null;
  params: {
    buy_op: string; buy_threshold: number;
    sell_op: string; sell_threshold: number;
    user_indicator_id: number; sub_key: string;
  } | null;
  startDate: string;
  endDate: string;
}

function TradeDetailDrawer({ open, onClose, onAfterClose, trade, params, startDate, endDate }: TradeDetailDrawerProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const [loading, setLoading] = useState(false);
  const [chartData, setChartData] = useState<TradeChartOut | null>(null);
  const isMobile = useIsMobile();

  // 每次打开或切换 trade 时重新拉数据
  useEffect(() => {
    if (!open || !trade || !params) { setChartData(null); return; }
    setLoading(true);
    setChartData(null);
    fetchTradeChart({
      ts_code: trade.ts_code,
      user_indicator_id: params.user_indicator_id,
      sub_key: params.sub_key,
      start_date: startDate,
      end_date: endDate,
    })
      .then(setChartData)
      .catch(() => message.error("获取验证图数据失败"))
      .finally(() => setLoading(false));
  }, [open, trade, params, startDate, endDate]);

  // 数据到位后渲染 ECharts
  useEffect(() => {
    if (!chartData || !chartRef.current || !trade || !params) return;
    if (!chartInst.current) {
      chartInst.current = echarts.init(chartRef.current);
    }
    const chart = chartInst.current;

    const dates  = chartData.bars.map((b) => b.time);
    // ECharts candlestick 格式：[open, close, low, high]
    const ohlc   = chartData.bars.map((b) => [b.open, b.close, b.low, b.high]);
    const indVals = chartData.indicator.map((p) => p.value ?? null);

    // 买卖标记点（定位在当日最低/最高价外侧）
    const barByDate = new Map(chartData.bars.map((b) => [b.time, b]));
    const markerData: object[] = [];
    const buyBar  = barByDate.get(trade.buy_date);
    if (buyBar) {
      markerData.push({
        value: [trade.buy_date, +(buyBar.low * 0.985).toFixed(3)],
        itemStyle: { color: "#52c41a" },
        symbol: "triangle",
        symbolSize: 14,
        label: { show: true, formatter: "买", position: "bottom", color: "#52c41a", fontSize: 11, fontWeight: 700 },
      });
    }
    if (trade.sell_date) {
      const sellBar = barByDate.get(trade.sell_date);
      if (sellBar) {
        markerData.push({
          value: [trade.sell_date, +(sellBar.high * 1.015).toFixed(3)],
          itemStyle: { color: "#f5222d" },
          symbol: "triangle",
          symbolSize: 14,
          symbolRotate: 180,
          label: { show: true, formatter: "卖", position: "top", color: "#f5222d", fontSize: 11, fontWeight: 700 },
        });
      }
    }

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }] },
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 60, right: 16, top: 16, height: "52%" },
        { left: 60, right: 16, top: "72%", bottom: 40 },
      ],
      xAxis: [
        {
          type: "category", data: dates, gridIndex: 0,
          axisLabel: { show: false }, axisLine: { lineStyle: { color: "#333" } }, axisTick: { show: false },
        },
        {
          type: "category", data: dates, gridIndex: 1,
          axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } },
        },
      ],
      yAxis: [
        {
          scale: true, gridIndex: 0,
          axisLabel: { color: "#8c8c8c", fontSize: 10 },
          splitLine: { lineStyle: { color: "#222" } },
        },
        {
          gridIndex: 1,
          axisLabel: { color: "#8c8c8c", fontSize: 10 },
          splitLine: { lineStyle: { color: "#222" } },
        },
      ],
      dataZoom: [
        { type: "inside",  xAxisIndex: [0, 1], start: 0, end: 100 },
        { type: "slider",  xAxisIndex: [0, 1], bottom: 4, height: 18,
          textStyle: { color: "#8c8c8c", fontSize: 10 }, handleStyle: { color: "#555" }, fillerColor: "rgba(80,80,80,0.2)" },
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          xAxisIndex: 0, yAxisIndex: 0,
          data: ohlc,
          itemStyle: {
            color: RISE_COLOR, color0: FALL_COLOR,
            borderColor: RISE_COLOR, borderColor0: FALL_COLOR,
          },
        },
        {
          name: "买卖点",
          type: "scatter",
          xAxisIndex: 0, yAxisIndex: 0,
          data: markerData,
          z: 10,
          symbolSize: 14,
          tooltip: { show: false },
        },
        {
          name: chartData.sub_display_name,
          type: "line",
          xAxisIndex: 1, yAxisIndex: 1,
          data: indVals,
          lineStyle: { color: "#4096ff", width: 1.5 },
          symbol: "none",
          connectNulls: false,
          markLine: {
            symbol: ["none", "none"],
            silent: true,
            data: [
              {
                name: `买入 ${OP_SYMBOL[params.buy_op] ?? params.buy_op}${params.buy_threshold}`,
                yAxis: params.buy_threshold,
                lineStyle: { color: "#52c41a", type: "dashed", width: 1.5 },
                label: { formatter: `买 ${OP_SYMBOL[params.buy_op] ?? params.buy_op}${params.buy_threshold}`, color: "#52c41a", fontSize: 10 },
              },
              {
                name: `卖出 ${OP_SYMBOL[params.sell_op] ?? params.sell_op}${params.sell_threshold}`,
                yAxis: params.sell_threshold,
                lineStyle: { color: "#f5222d", type: "dashed", width: 1.5 },
                label: { formatter: `卖 ${OP_SYMBOL[params.sell_op] ?? params.sell_op}${params.sell_threshold}`, color: "#f5222d", fontSize: 10 },
              },
            ],
          },
        },
      ],
    }, true);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [chartData, trade, params]);

  // Drawer 关闭时销毁图表实例
  useEffect(() => {
    if (!open && chartInst.current) {
      chartInst.current.dispose();
      chartInst.current = null;
    }
  }, [open]);

  const subName = chartData?.sub_display_name ?? params?.sub_key ?? "";

  return (
    <Drawer
      title={
        trade ? (
          <Space>
            <span>{trade.ts_code}</span>
            {trade.name && <Text type="secondary" style={{ fontSize: 13 }}>{trade.name}</Text>}
            <Tag color={trade.pnl_pct != null && trade.pnl_pct > 0 ? "success" : "error"}>
              {fmtPct(trade.pnl_pct)}
            </Tag>
          </Space>
        ) : "交易验证"
      }
      open={open}
      onClose={onClose}
      afterOpenChange={(vis) => { if (!vis) onAfterClose?.(); }}
      width={Math.min(860, window.innerWidth * 0.95)}
      styles={{ body: { padding: "12px 16px", background: "#141414" } }}
    >
      {trade && params && (
        <Space direction="vertical" style={{ width: "100%" }} size={10}>
          {/* 触发条件信息卡 */}
          <Row gutter={8}>
            <Col span={12}>
              <div style={{ background: "#1a2e1a", border: "1px solid #2d5a2d", borderRadius: 6, padding: "8px 12px" }}>
                <Text type="secondary" style={{ fontSize: 11 }}>买入触发</Text>
                <div style={{ marginTop: 2 }}>
                  <Text style={{ fontWeight: 700, color: "#52c41a", fontSize: 18 }}>
                    {trade.buy_trigger_val != null ? trade.buy_trigger_val.toFixed(4) : "—"}
                  </Text>
                  {params && (
                    <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                      {subName} {OP_SYMBOL[params.buy_op] ?? params.buy_op} {params.buy_threshold} ✓
                    </Text>
                  )}
                </div>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {trade.buy_date} · 收盘价 ¥{trade.buy_price.toFixed(3)}
                </Text>
              </div>
            </Col>
            <Col span={12}>
              {trade.sell_date ? (
                <div style={{ background: "#2e1a1a", border: "1px solid #5a2d2d", borderRadius: 6, padding: "8px 12px" }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>卖出触发</Text>
                  <div style={{ marginTop: 2 }}>
                    <Text style={{ fontWeight: 700, color: "#f5222d", fontSize: 18 }}>
                      {trade.sell_trigger_val != null ? trade.sell_trigger_val.toFixed(4) : "—"}
                    </Text>
                    {params && (
                      <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                        {subName} {OP_SYMBOL[params.sell_op] ?? params.sell_op} {params.sell_threshold} ✓
                      </Text>
                    )}
                  </div>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {trade.sell_date} · 收盘价 ¥{(trade.sell_price ?? 0).toFixed(3)}
                    {" · "}
                    <span style={{ color: pnlColor(trade.pnl_pct) }}>{fmtPct(trade.pnl_pct)}</span>
                  </Text>
                </div>
              ) : (
                <div style={{ background: "#2e2a14", border: "1px solid #5a4e14", borderRadius: 6, padding: "8px 12px" }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>持有中</Text>
                  <div style={{ marginTop: 2 }}>
                    <Text style={{ color: "#faad14" }}>尚未触发卖出条件</Text>
                  </div>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    等待 {subName} {OP_SYMBOL[params.sell_op] ?? params.sell_op} {params.sell_threshold}
                  </Text>
                </div>
              )}
            </Col>
          </Row>

          {/* 图表区 */}
          <div style={{ background: "#0d0d0d", borderRadius: 6, padding: "8px 4px" }}>
            <Spin spinning={loading} tip="加载图表...">
              {chartData ? (
                <div ref={chartRef} style={{ width: "100%", height: isMobile ? 280 : 440 }} />
              ) : !loading ? (
                <div style={{ height: isMobile ? 280 : 440, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <Text type="secondary">暂无数据</Text>
                </div>
              ) : (
                <div style={{ height: isMobile ? 280 : 440 }} />
              )}
            </Spin>
            <div style={{ paddingLeft: 8, marginTop: 2 }}>
              <Text type="secondary" style={{ fontSize: 10 }}>
                上图：K线（▲买入 ▼卖出）· 下图：{subName}（绿虚线=买入阈值 红虚线=卖出阈值）
              </Text>
            </div>
          </div>
        </Space>
      )}
    </Drawer>
  );
}

export default function BacktestPage() {
  const [form] = Form.useForm();
  const isMobile = useIsMobile();

  // 从选股页跳转过来时，location.state 中携带选股条件，自动预填表单
  const location = useLocation();
  const navigate = useNavigate();
  type FromScreeningState = {
    user_indicator_id: number;
    sub_key: string | null;
    buy_op: string;
    buy_threshold: number;
  };
  // useRef 保证只消费一次：指标加载完后填入，然后置 null 防止重复触发
  const fromScreeningRef = useRef<FromScreeningState | null>(
    (location.state as { from_screening?: FromScreeningState } | null)?.from_screening ?? null,
  );

  const [indicators, setIndicators] = useState<UserIndicatorOut[]>([]);
  const [loadingInd, setLoadingInd] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestRunOut | null>(null);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [activeTemplate, setActiveTemplate] = useState<StrategyTemplate | null>(null);

  // 最近一次回测的参数（供 Drawer 使用）
  const [lastParams, setLastParams] = useState<{
    buy_op: string; buy_threshold: number;
    sell_op: string; sell_threshold: number;
    user_indicator_id: number; sub_key: string;
    start_date: string; end_date: string;
  } | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<BacktestTradeRow | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const scrollYRef = useRef(0);

  const selectedId = Form.useWatch("user_indicator_id", form);
  const selectedInd = useMemo(
    () => indicators.find((x) => x.id === selectedId) ?? null,
    [indicators, selectedId],
  );
  const subOpts = useMemo(() => {
    if (!selectedInd) return [];
    if (selectedInd.kind === "dsl" && selectedInd.definition) {
      return screeningSubKeys(selectedInd.definition);
    }
    return [];
  }, [selectedInd]);

  // 模板模式下的子线显示名（从已加载指标定义中查找）
  const templateSubName = useMemo(() => {
    if (!activeTemplate) return "";
    const matchedInd = indicators.find((x) => x.code === activeTemplate.indicatorCode);
    if (!matchedInd?.definition) return activeTemplate.subKey;
    const subs = (matchedInd.definition as Record<string, unknown>)?.sub_indicators as
      { key?: string; name?: string }[] | undefined;
    if (!Array.isArray(subs)) return activeTemplate.subKey;
    const found = subs.find((s) => s.key === activeTemplate.subKey);
    return found?.name || activeTemplate.subKey;
  }, [activeTemplate, indicators]);

  const loadIndicators = useCallback(async () => {
    setLoadingInd(true);
    try {
      const rows = await fetchCustomIndicators();
      setIndicators(rows.filter((r) => r.kind === "dsl"));
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoadingInd(false);
    }
  }, []);

  useEffect(() => { void loadIndicators(); }, [loadIndicators]);

  // 指标列表加载完成后，若携带了来自选股页的参数则自动填入
  useEffect(() => {
    if (!indicators.length || !fromScreeningRef.current) return;
    const { user_indicator_id, sub_key, buy_op, buy_threshold } = fromScreeningRef.current;
    fromScreeningRef.current = null; // 只消费一次
    form.setFieldsValue({
      user_indicator_id,
      sub_key: sub_key ?? undefined,
      buy_op,
      buy_threshold,
    });
    message.info("已从选股页导入指标条件，请补充卖出条件后开始回测");
  }, [indicators, form]);

  const handleApplyTemplate = useCallback((tpl: StrategyTemplate) => {
    // 在已加载的指标列表里找到与模板对应的预置指标
    const matched = indicators.find((x) => x.code === tpl.indicatorCode);
    form.setFieldsValue({
      buy_op: tpl.buy_op,
      buy_threshold: tpl.buy_threshold,
      sell_op: tpl.sell_op,
      sell_threshold: tpl.sell_threshold,
      max_positions: tpl.max_positions,
      ...(matched ? { user_indicator_id: matched.id, sub_key: tpl.subKey } : {}),
    });
    setActiveTemplate(tpl);
    if (matched) {
      message.success(`已套用「${tpl.name}」模板`);
    } else {
      message.warning(`已套用「${tpl.name}」模板，但未找到预置指标（请重启后端以生成预置指标）`);
    }
  }, [form, indicators]);

  useEffect(() => {
    if (!selectedInd) { form.setFieldValue("sub_key", undefined); return; }
    const first = subOpts[0]?.value;
    if (first != null && form.getFieldValue("sub_key") == null) {
      form.setFieldValue("sub_key", first);
    }
  }, [selectedInd, subOpts, form]);

  // 渲染资金曲线（权益 + 回撤双轴）
  useEffect(() => {
    if (!result || !chartRef.current) return;
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }
    const chart = chartInstance.current;
    const dates   = result.equity_curve.map((pt) => pt.date);
    const equities = result.equity_curve.map((pt) => pt.equity);
    const drawdowns = result.equity_curve.map((pt) => pt.drawdown_pct);

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        formatter: (params: echarts.TooltipComponentFormatterCallbackParams) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const date = String((params[0] as any).axisValue ?? params[0].name ?? "");
          const lines = params.map((p) => {
            const name = p.seriesName ?? "";
            const val = typeof p.value === "number" ? p.value : Number(p.value);
            const fmt = name === "回撤%" ? `${val.toFixed(2)}%` : `¥${fmtMoney(val)}`;
            return `<span style="display:inline-block;margin-right:4px;border-radius:10px;width:8px;height:8px;background:${p.color}"></span>${name}: ${fmt}`;
          });
          return `${date}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: {
        data: ["总权益", "回撤%"],
        top: 8,
        textStyle: { color: "#d9d9d9", fontSize: 12 },
      },
      grid: [
        { left: 70, right: 20, top: 48, bottom: 120 },
        { left: 70, right: 20, top: "68%", bottom: 40 },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#333" } } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      ],
      yAxis: [
        {
          type: "value", gridIndex: 0,
          axisLabel: { color: "#8c8c8c", formatter: (v: number) => `¥${(v / 10000).toFixed(0)}万` },
          splitLine: { lineStyle: { color: "#222" } },
        },
        {
          type: "value", gridIndex: 1,
          axisLabel: { color: "#8c8c8c", formatter: (v: number) => `${v.toFixed(1)}%` },
          splitLine: { lineStyle: { color: "#222" } },
        },
      ],
      series: [
        {
          name: "总权益",
          type: "line",
          xAxisIndex: 0, yAxisIndex: 0,
          data: equities,
          smooth: false,
          lineStyle: { color: "#1677ff", width: 2 },
          itemStyle: { color: "#1677ff" },
          symbol: "none",
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(22,119,255,0.25)" },
            { offset: 1, color: "rgba(22,119,255,0.02)" },
          ]) },
        },
        {
          name: "回撤%",
          type: "line",
          xAxisIndex: 1, yAxisIndex: 1,
          data: drawdowns,
          smooth: false,
          lineStyle: { color: FALL_COLOR, width: 1.5 },
          itemStyle: { color: FALL_COLOR },
          symbol: "none",
          areaStyle: { color: "rgba(255,77,79,0.12)" },
        },
      ],
    });

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [result]);

  const tradeColumns: ColumnsType<BacktestTradeRow> = [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 110,
      render: (v: string) => <Link to={`/?ts_code=${encodeURIComponent(v)}`}>{v}</Link>,
    },
    { title: "名称", dataIndex: "name", width: 90, ellipsis: true },
    { title: "买入日", dataIndex: "buy_date", width: 100 },
    { title: "买入价", dataIndex: "buy_price", width: 80, align: "right", render: (v: number) => v.toFixed(3) },
    {
      title: "卖出日",
      dataIndex: "sell_date",
      width: 100,
      render: (v: string | null) => v == null ? <Tag color="orange">持有中</Tag> : v,
    },
    { title: "卖出价", dataIndex: "sell_price", width: 80, align: "right", render: (v: number | null) => v == null ? "—" : v.toFixed(3) },
    {
      title: "盈亏额",
      dataIndex: "pnl",
      width: 100,
      align: "right",
      render: (v: number | null) =>
        v == null ? "—" : (
          <span style={{ color: pnlColor(v) }}>
            {v > 0 ? "+" : ""}{fmtMoney(v)}
          </span>
        ),
    },
    {
      title: "盈亏%",
      dataIndex: "pnl_pct",
      width: 85,
      align: "right",
      render: (v: number | null) =>
        v == null ? "—" : (
          <span style={{ color: pnlColor(v) }}>{fmtPct(v)}</span>
        ),
    },
    {
      title: "触发值",
      key: "trigger",
      width: 100,
      render: (_: unknown, row: BacktestTradeRow) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 11 }}>
            <span style={{ color: "#52c41a", fontWeight: 600 }}>买</span>{" "}
            {row.buy_trigger_val != null ? row.buy_trigger_val.toFixed(3) : "—"}
          </Text>
          {row.sell_date ? (
            <Text style={{ fontSize: 11 }}>
              <span style={{ color: "#f5222d", fontWeight: 600 }}>卖</span>{" "}
              {row.sell_trigger_val != null ? row.sell_trigger_val.toFixed(3) : "—"}
            </Text>
          ) : (
            <Text type="secondary" style={{ fontSize: 10 }}>持有中</Text>
          )}
        </Space>
      ),
    },
    {
      title: "",
      key: "detail",
      width: 36,
      render: (_: unknown, row: BacktestTradeRow) => (
        <Tooltip title="查看K线验证图">
          <LineChartOutlined
            style={{ cursor: "pointer", color: "#4096ff", fontSize: 15 }}
            onClick={(e) => { e.stopPropagation(); scrollYRef.current = window.scrollY; setSelectedTrade(row); setDrawerOpen(true); }}
          />
        </Tooltip>
      ),
    },
  ];

  const onRun = async () => {
    try {
      const v = await form.validateFields();
      const [start, end] = v.date_range as [Dayjs, Dayjs];
      const startStr = start.format("YYYY-MM-DD");
      const endStr   = end.format("YYYY-MM-DD");
      setRunning(true);
      setResult(null);
      if (chartInstance.current) chartInstance.current.clear();
      try {
        const out = await runBacktest({
          start_date: startStr,
          end_date: endStr,
          user_indicator_id: v.user_indicator_id,
          sub_key: v.sub_key ?? null,
          buy_op: v.buy_op,
          buy_threshold: v.buy_threshold,
          sell_op: v.sell_op,
          sell_threshold: v.sell_threshold,
          initial_capital: v.initial_capital,
          max_positions: v.max_positions,
          max_scan: v.max_scan ?? 3000,
        });
        setResult(out);
        setLastParams({
          buy_op: v.buy_op, buy_threshold: v.buy_threshold,
          sell_op: v.sell_op, sell_threshold: v.sell_threshold,
          user_indicator_id: v.user_indicator_id,
          sub_key: v.sub_key ?? "",
          start_date: startStr, end_date: endStr,
        });
        const sign = out.total_return_pct > 0 ? "+" : "";
        message.success(`回测完成：${out.total_trades} 笔交易，总收益 ${sign}${out.total_return_pct.toFixed(2)}%`);
      } catch (e) {
        message.error(getApiErrorMessage(e));
      } finally {
        setRunning(false);
      }
    } catch {
      // 表单校验失败
    }
  };

  const retColor = result ? pnlColor(result.total_return_pct) : undefined;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1400, margin: "0 auto" }}>
      {/* 页头 */}
      <div>
        <Title level={4} style={{ margin: 0 }}>条件选股回测</Title>
        <Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
          基于自定义指标对全市场逐日扫描：满足买入条件建仓，满足卖出条件平仓。
          等额分配资金，最多同时持有指定只数。
        </Paragraph>
      </div>

      {/* 配置区 */}
      <Card
        title="回测配置"
        styles={{ body: { paddingBottom: 8 } }}
        extra={
          activeTemplate ? (
            <Space size={8}>
              <Button
                type="link"
                size="small"
                style={{ padding: 0, color: "#8c8c8c" }}
                onClick={() => setActiveTemplate(null)}
              >
                自定义配置
              </Button>
              <Button
                icon={<AppstoreOutlined />}
                size="small"
                onClick={() => setTemplateOpen(true)}
              >
                更换模板
              </Button>
            </Space>
          ) : (
            <Button
              icon={<AppstoreOutlined />}
              size="small"
              onClick={() => setTemplateOpen(true)}
            >
              策略模板
            </Button>
          )
        }
      >
        {loadingInd ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : (
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              date_range: [dayjs().subtract(1, "year"), dayjs()],
              buy_op: "gt",
              buy_threshold: 0,
              sell_op: "lt",
              sell_threshold: 0,
              initial_capital: 100000,
              max_positions: 3,
              max_scan: 3000,
            }}
          >
            {activeTemplate ? (
              /* ── 模板模式：简化表单 ── */
              <>
                {/* 模板信息横幅 */}
                <div style={{
                  background: "#111b2e",
                  border: "1px solid #1d3461",
                  borderRadius: 8,
                  padding: "10px 16px",
                  marginBottom: 16,
                }}>
                  <Space size={8} style={{ marginBottom: 4 }}>
                    <Text strong style={{ fontSize: 14 }}>{activeTemplate.name}</Text>
                    <Tag color={BADGE_COLOR[activeTemplate.badge] ?? "default"} style={{ fontSize: 11 }}>
                      {activeTemplate.badge}
                    </Tag>
                  </Space>
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>{activeTemplate.desc}</Text>
                  </div>
                </div>

                {/* 隐藏字段（由模板填充，不展示给用户） */}
                <Form.Item name="user_indicator_id" hidden><InputNumber /></Form.Item>
                <Form.Item name="sub_key" hidden><Input /></Form.Item>
                <Form.Item name="buy_op" hidden><Input /></Form.Item>
                <Form.Item name="sell_op" hidden><Input /></Form.Item>

                <Row gutter={[16, 0]}>
                  <Col xs={24} md={8}>
                    <Form.Item name="date_range" label="回测时间范围" rules={[{ required: true, message: "请选择时间范围" }]}>
                      <DatePicker.RangePicker style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={4}>
                    <Form.Item
                      name="buy_threshold"
                      label={
                        <span>
                          {templateSubName}{" "}
                          <Text style={{ color: "#52c41a", fontWeight: 600 }}>
                            {OP_SYMBOL[activeTemplate.buy_op] ?? activeTemplate.buy_op}
                          </Text>
                          {" "}阈值<Text type="secondary" style={{ fontSize: 11 }}>（买入）</Text>
                        </span>
                      }
                    >
                      <InputNumber step={0.0001} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={4}>
                    <Form.Item
                      name="sell_threshold"
                      label={
                        <span>
                          {templateSubName}{" "}
                          <Text style={{ color: "#f5222d", fontWeight: 600 }}>
                            {OP_SYMBOL[activeTemplate.sell_op] ?? activeTemplate.sell_op}
                          </Text>
                          {" "}阈值<Text type="secondary" style={{ fontSize: 11 }}>（卖出）</Text>
                        </span>
                      }
                    >
                      <InputNumber step={0.0001} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={4}>
                    <Form.Item name="initial_capital" label="初始资金（元）">
                      <InputNumber
                        min={1000} step={10000}
                        formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={2}>
                    <Form.Item name="max_positions" label="最大持仓（只）">
                      <InputNumber min={1} max={10} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={2}>
                    <Form.Item name="max_scan" label="扫描只数" tooltip="每日最多扫描的股票数，越大越慢">
                      <InputNumber min={100} max={8000} step={500} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={2} style={{ display: "flex", alignItems: "flex-end" }}>
                    <Form.Item style={{ marginBottom: 24, width: "100%" }}>
                      <Button type="primary" onClick={() => void onRun()} loading={running} block>
                        {running ? "回测中…" : "开始回测"}
                      </Button>
                    </Form.Item>
                  </Col>
                </Row>
              </>
            ) : (
              /* ── 自定义模式：完整表单 ── */
              <>
                <Row gutter={[16, 0]}>
                  <Col xs={24} md={8}>
                    <Form.Item name="date_range" label="回测时间范围" rules={[{ required: true, message: "请选择时间范围" }]}>
                      <DatePicker.RangePicker style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item name="user_indicator_id" label="自定义指标" rules={[{ required: true, message: "请选择指标" }]}>
                      <Select
                        placeholder="选择已保存的指标"
                        options={indicators.map((r) => ({ value: r.id, label: `${r.display_name} (${r.code})` }))}
                        showSearch
                        optionFilterProp="label"
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  </Col>
                  {subOpts.length > 0 && (
                    <Col xs={24} md={8}>
                      <Form.Item name="sub_key" label="参与回测的子线" rules={[{ required: true, message: "请选择子线" }]}>
                        <Select placeholder="选择子线" options={subOpts} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  )}
                </Row>

                <Row gutter={[16, 0]}>
                  <Col xs={12} md={3}>
                    <Form.Item name="buy_op" label="买入条件">
                      <Select options={compareOptions} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={3}>
                    <Form.Item name="buy_threshold" label="买入阈值">
                      <InputNumber step={0.0001} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={3}>
                    <Form.Item name="sell_op" label="卖出条件">
                      <Select options={compareOptions} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={3}>
                    <Form.Item name="sell_threshold" label="卖出阈值">
                      <InputNumber step={0.0001} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={4}>
                    <Form.Item name="initial_capital" label="初始资金（元）">
                      <InputNumber
                        min={1000} step={10000}
                        formatter={(v) => `${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}
                        style={{ width: "100%" }}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={3}>
                    <Form.Item name="max_positions" label="最大持仓（只）">
                      <InputNumber min={1} max={10} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={3}>
                    <Form.Item name="max_scan" label="最多扫描只数" tooltip="每日最多扫描的股票数，越大越慢">
                      <InputNumber min={100} max={8000} step={500} style={{ width: "100%" }} />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={2} style={{ display: "flex", alignItems: "flex-end" }}>
                    <Form.Item style={{ marginBottom: 24, width: "100%" }}>
                      <Button type="primary" onClick={() => void onRun()} loading={running} block>
                        {running ? "回测中…" : "开始回测"}
                      </Button>
                    </Form.Item>
                  </Col>
                </Row>
              </>
            )}
          </Form>
        )}
      </Card>

      {/* 结果区 */}
      {result && (        <Space direction="vertical" size="large" style={{ width: "100%" }}>

          {/* 快捷入口：将买入条件同步到选股页，在当前截面快速看哪些股票满足条件 */}
          <div style={{ textAlign: "right" }}>
            <Button
              size="small"
              icon={<SwapOutlined />}
              onClick={() => {
                if (!lastParams) return;
                navigate("/screening", {
                  state: {
                    from_backtest: {
                      user_indicator_id: lastParams.user_indicator_id,
                      sub_key: lastParams.sub_key || null,
                      compare_op: lastParams.buy_op,
                      threshold: lastParams.buy_threshold,
                    },
                  },
                });
              }}
            >
              将买入条件转为选股
            </Button>
          </div>

          {/* 绩效总览卡片组 */}
          <Row gutter={[16, 16]}>
            {/* 核心收益 */}
            <Col xs={24} md={8}>
              <Card
                size="small"
                style={{ height: "100%" }}
                styles={{ body: { padding: "16px 20px" } }}
              >
                <Space align="start">
                  <RiseOutlined style={{ fontSize: 28, color: "#1677ff", marginTop: 4 }} />
                  <div>
                    <Text type="secondary" style={{ fontSize: 12 }}>收益概览</Text>
                    <div style={{ marginTop: 4 }}>
                      <Text style={{ fontSize: 28, fontWeight: 700, color: retColor }}>
                        {result.total_return_pct > 0 ? "+" : ""}{result.total_return_pct.toFixed(2)}%
                      </Text>
                      <Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>总收益率</Text>
                    </div>
                    <div style={{ marginTop: 8, display: "flex", gap: 24 }}>
                      <div>
                        <Text type="secondary" style={{ fontSize: 11 }}>年化收益</Text>
                        <div style={{ fontWeight: 600, color: pnlColor(result.annualized_return) }}>
                          {fmtPct(result.annualized_return)}
                        </div>
                      </div>
                      <div>
                        <Text type="secondary" style={{ fontSize: 11 }}>初始资金</Text>
                        <div style={{ fontWeight: 600 }}>¥{fmtMoney(result.initial_capital)}</div>
                      </div>
                      <div>
                        <Text type="secondary" style={{ fontSize: 11 }}>最终权益</Text>
                        <div style={{ fontWeight: 600, color: retColor }}>¥{fmtMoney(result.final_equity)}</div>
                      </div>
                    </div>
                  </div>
                </Space>
              </Card>
            </Col>

            {/* 风险控制 */}
            <Col xs={24} md={8}>
              <Card size="small" style={{ height: "100%" }} styles={{ body: { padding: "16px 20px" } }}>
                <Space align="start">
                  <FallOutlined style={{ fontSize: 28, color: FALL_COLOR, marginTop: 4 }} />
                  <div style={{ width: "100%" }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>风险控制</Text>
                    <Row gutter={16} style={{ marginTop: 8 }}>
                      <Col span={12}>
                        <MetricCard
                          title="最大回撤"
                          value={result.max_drawdown_pct}
                          suffix="%"
                          color={FALL_COLOR}
                          hint="从历史最高点到最低点的最大跌幅，衡量极端风险"
                        />
                      </Col>
                      <Col span={12}>
                        <MetricCard
                          title="卡玛比率"
                          value={result.calmar_ratio}
                          hint="年化收益 / |最大回撤|，越高说明单位风险获取的收益越多"
                          color={result.calmar_ratio != null && result.calmar_ratio > 1 ? RISE_COLOR : undefined}
                        />
                      </Col>
                    </Row>
                    <Row gutter={16} style={{ marginTop: 12 }}>
                      <Col span={12}>
                        <MetricCard
                          title="夏普比率"
                          value={result.sharpe_ratio}
                          hint="日超额收益均值 / 日收益标准差 × √252，衡量风险调整后收益"
                          color={result.sharpe_ratio != null && result.sharpe_ratio > 1 ? RISE_COLOR : undefined}
                          precision={3}
                        />
                      </Col>
                      <Col span={12}>
                        <MetricCard
                          title="盈亏比"
                          value={result.profit_factor}
                          hint="总盈利 / |总亏损|，> 1 表示总体盈利"
                          color={result.profit_factor != null && result.profit_factor > 1 ? RISE_COLOR : FALL_COLOR}
                          precision={3}
                        />
                      </Col>
                    </Row>
                  </div>
                </Space>
              </Card>
            </Col>

            {/* 交易统计 */}
            <Col xs={24} md={8}>
              <Card size="small" style={{ height: "100%" }} styles={{ body: { padding: "16px 20px" } }}>
                <Space align="start">
                  <TrophyOutlined style={{ fontSize: 28, color: "#faad14", marginTop: 4 }} />
                  <div style={{ width: "100%" }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>交易统计</Text>
                    <Row gutter={16} style={{ marginTop: 8 }}>
                      <Col span={8}>
                        <Statistic
                          title={<Text type="secondary" style={{ fontSize: 11 }}>总笔数</Text>}
                          value={result.total_trades}
                          valueStyle={{ fontSize: 20 }}
                        />
                      </Col>
                      <Col span={8}>
                        <Statistic
                          title={<Text type="secondary" style={{ fontSize: 11 }}>胜率</Text>}
                          value={result.win_rate ?? "—"}
                          suffix={result.win_rate != null ? "%" : ""}
                          precision={result.win_rate != null ? 1 : 0}
                          valueStyle={{ fontSize: 20, color: result.win_rate != null && result.win_rate >= 50 ? RISE_COLOR : undefined }}
                        />
                      </Col>
                      <Col span={8}>
                        <MetricCard
                          title="平均持仓"
                          value={result.avg_holding_days}
                          suffix="天"
                          hint="已平仓交易的平均持有自然日天数"
                          precision={1}
                        />
                      </Col>
                    </Row>
                    <Divider style={{ margin: "10px 0" }} />
                    <Row gutter={8}>
                      <Col span={6}>
                        <Text type="secondary" style={{ fontSize: 11 }}>盈 {result.total_win} 笔</Text>
                      </Col>
                      <Col span={6}>
                        <Text type="secondary" style={{ fontSize: 11 }}>亏 {result.total_loss} 笔</Text>
                      </Col>
                      <Col span={6}>
                        <Text style={{ fontSize: 11, color: RISE_COLOR }}>
                          均盈 {fmtPct(result.avg_win_pct)}
                        </Text>
                      </Col>
                      <Col span={6}>
                        <Text style={{ fontSize: 11, color: FALL_COLOR }}>
                          均亏 {result.avg_loss_pct != null ? fmtPct(-result.avg_loss_pct) : "—"}
                        </Text>
                      </Col>
                    </Row>
                    <Row gutter={8} style={{ marginTop: 4 }}>
                      <Col span={12}>
                        <Text style={{ fontSize: 11, color: RISE_COLOR }}>
                          最大单笔盈利 {fmtPct(result.max_win_pct)}
                        </Text>
                      </Col>
                      <Col span={12}>
                        <Text style={{ fontSize: 11, color: FALL_COLOR }}>
                          最大单笔亏损 {fmtPct(result.max_loss_pct)}
                        </Text>
                      </Col>
                    </Row>
                  </div>
                </Space>
              </Card>
            </Col>
          </Row>

          {/* 备注 */}
          {result.note && (
            <Text type="warning">⚠ {result.note}</Text>
          )}

          {/* 资金曲线 */}
          <Card
            title={
              <Space>
                <LineChartOutlined />
                <span>资金曲线</span>
                <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                  {result.start_date} ~ {result.end_date}｜扫描 {result.scanned_stocks} 只
                </Text>
              </Space>
            }
          >
            <div ref={chartRef} style={{ width: "100%", height: isMobile ? 280 : 440 }} />
          </Card>

          {/* 交易记录 */}
          <Card
            title={
              <Space>
                <span>交易记录</span>
                <Tag color="blue">{result.total_trades} 笔</Tag>
                <Tag color="green">盈 {result.total_win}</Tag>
                <Tag color="red">亏 {result.total_loss}</Tag>
              </Space>
            }
          >
            <Table<BacktestTradeRow>
              rowKey={(r) => `${r.ts_code}-${r.buy_date}`}
              size="small"
              columns={tradeColumns}
              dataSource={result.trades}
              rowClassName={zebraRowClass}
              pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 笔` }}
              scroll={{ x: "max-content" }}
              onRow={(row) => ({
                style: { cursor: "pointer" },
                onClick: () => { scrollYRef.current = window.scrollY; setSelectedTrade(row); setDrawerOpen(true); },
              })}
            />
          </Card>

        </Space>
      )}

      {/* 策略模板弹窗 */}
      <TemplatePanel
        open={templateOpen}
        onClose={() => setTemplateOpen(false)}
        onApply={handleApplyTemplate}
      />

      {/* 交易K线验证 Drawer */}
      <TradeDetailDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onAfterClose={() => requestAnimationFrame(() => window.scrollTo(0, scrollYRef.current))}
        trade={selectedTrade}
        params={lastParams}
        startDate={lastParams?.start_date ?? ""}
        endDate={lastParams?.end_date ?? ""}
      />
    </Space>
  );
}
