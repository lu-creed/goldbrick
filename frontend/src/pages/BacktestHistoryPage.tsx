/**
 * 回测历史记录页
 *
 * 展示所有历史回测记录的列表，每条记录包含回测参数摘要和关键绩效指标。
 * 点击记录可查看完整详情：资金曲线、交易明细，以及每笔交易的 K 线验证图。
 *
 * 注意：回测由「开始回测」页面发起并自动保存，此页面只负责读取和删除历史记录。
 */
import * as echarts from "echarts";
import {
  Button,
  Card,
  Col,
  Divider,
  Drawer,
  Popconfirm,
  Row,
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
  FallOutlined,
  InfoCircleOutlined,
  LineChartOutlined,
  BarChartOutlined,
  RiseOutlined,
  TrophyOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { Key } from "react";
import dayjs from "dayjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  deleteBacktestRecord,
  fetchBacktestRecordDetail,
  fetchBacktestRecords,
  fetchTradeChart,
  getApiErrorMessage,
  type BacktestRecordDetail,
  type BacktestRecordItem,
  type BacktestTradeRow,
  type TradeChartOut,
} from "../api/client";
import { ECHARTS_BASE_OPTION, FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";
import { useIsMobile } from "../hooks/useIsMobile";

const { Title, Text, Paragraph } = Typography;

// ── 工具函数 ──────────────────────────────────────────────────────────────────

/** 格式化金额（千分符 + 2位小数） */
function fmtMoney(v: number) {
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 带正负号的百分比格式化，null 时返回破折号 */
function fmtPct(v: number | null | undefined, precision = 2): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(precision)}%`;
}

/** 根据数值正负返回对应的涨跌颜色 */
function pnlColor(v: number | null | undefined): string | undefined {
  if (v == null) return undefined;
  return v > 0 ? RISE_COLOR : v < 0 ? FALL_COLOR : FLAT_COLOR;
}

/** 比较运算符 → 可读符号 */
const OP_SYMBOL: Record<string, string> = {
  gt: ">", gte: "≥", lt: "<", le: "≤", eq: "=", ne: "≠",
};

// ── 绩效指标小卡片 ─────────────────────────────────────────────────────────────

/**
 * MetricCard：带 Tooltip 说明的 Statistic 卡片
 * null 值时显示破折号；正数可指定颜色高亮
 */
function MetricCard({
  title, value, suffix = "", hint, color, precision = 2,
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

// ── K 线验证 Drawer（对单笔交易展示 K 线图 + 买卖标记 + 指标子线）────────────

interface TradeDetailDrawerProps {
  open: boolean;
  onClose: () => void;
  onAfterClose?: () => void;
  trade: BacktestTradeRow | null;
  /** 回测参数（用于请求 K 线验证图和绘制阈值线） */
  params: {
    buy_op: string; buy_threshold: number;
    sell_op: string; sell_threshold: number;
    user_indicator_id: number; sub_key: string;
  } | null;
  startDate: string;
  endDate: string;
}

function TradeDetailDrawer({
  open, onClose, onAfterClose, trade, params, startDate, endDate,
}: TradeDetailDrawerProps) {
  const chartRef  = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const [loading,   setLoading]   = useState(false);
  const [chartData, setChartData] = useState<TradeChartOut | null>(null);
  const isMobile = useIsMobile();

  // 每次打开或切换交易时拉数据
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

  // 数据到位后渲染图表
  useEffect(() => {
    if (!chartData || !chartRef.current || !trade || !params) return;
    if (!chartInst.current) {
      chartInst.current = echarts.init(chartRef.current);
    }
    const chart = chartInst.current;

    const dates   = chartData.bars.map((b) => b.time);
    // ECharts candlestick 格式：[open, close, low, high]
    const ohlc    = chartData.bars.map((b) => [b.open, b.close, b.low, b.high]);
    const indVals = chartData.indicator.map((p) => p.value ?? null);

    // 买卖标记点（三角形，定位在当日最低/最高价的略外侧）
    const barByDate   = new Map(chartData.bars.map((b) => [b.time, b]));
    const markerData: object[] = [];
    const buyBar = barByDate.get(trade.buy_date);
    if (buyBar) {
      markerData.push({
        value: [trade.buy_date, +(buyBar.low * 0.985).toFixed(3)],
        itemStyle: { color: "#52c41a" },
        symbol: "triangle", symbolSize: 14,
        label: { show: true, formatter: "买", position: "bottom", color: "#52c41a", fontSize: 11, fontWeight: 700 },
      });
    }
    if (trade.sell_date) {
      const sellBar = barByDate.get(trade.sell_date);
      if (sellBar) {
        markerData.push({
          value: [trade.sell_date, +(sellBar.high * 1.015).toFixed(3)],
          itemStyle: { color: "#f5222d" },
          symbol: "triangle", symbolSize: 14, symbolRotate: 180,
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
        backgroundColor: "#1f1f1f", borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 60, right: 16, top: 16, height: "52%" },
        { left: 60, right: 16, top: "72%", bottom: 40 },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0,
          axisLabel: { show: false }, axisLine: { lineStyle: { color: "#333" } }, axisTick: { show: false } },
        { type: "category", data: dates, gridIndex: 1,
          axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, axisLabel: { color: "#8c8c8c", fontSize: 10 }, splitLine: { lineStyle: { color: "#222" } } },
        { gridIndex: 1, axisLabel: { color: "#8c8c8c", fontSize: 10 }, splitLine: { lineStyle: { color: "#222" } } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 },
        { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 18,
          textStyle: { color: "#8c8c8c", fontSize: 10 }, handleStyle: { color: "#555" }, fillerColor: "rgba(80,80,80,0.2)" },
      ],
      series: [
        {
          name: "K线", type: "candlestick", xAxisIndex: 0, yAxisIndex: 0,
          data: ohlc,
          itemStyle: { color: RISE_COLOR, color0: FALL_COLOR, borderColor: RISE_COLOR, borderColor0: FALL_COLOR },
        },
        {
          name: "买卖点", type: "scatter", xAxisIndex: 0, yAxisIndex: 0,
          data: markerData, z: 10, symbolSize: 14, tooltip: { show: false },
        },
        {
          name: chartData.sub_display_name, type: "line", xAxisIndex: 1, yAxisIndex: 1,
          data: indVals, lineStyle: { color: "#4096ff", width: 1.5 },
          symbol: "none", connectNulls: false,
          markLine: {
            symbol: ["none", "none"], silent: true,
            data: [
              {
                yAxis: params.buy_threshold,
                lineStyle: { color: "#52c41a", type: "dashed", width: 1.5 },
                label: { formatter: `买 ${OP_SYMBOL[params.buy_op] ?? params.buy_op}${params.buy_threshold}`, color: "#52c41a", fontSize: 10 },
              },
              {
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

  // Drawer 关闭时销毁图表实例，释放内存
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
          {/* 买卖触发信息卡 */}
          <Row gutter={8}>
            <Col span={12}>
              <div style={{ background: "#1a2e1a", border: "1px solid #2d5a2d", borderRadius: 6, padding: "8px 12px" }}>
                <Text type="secondary" style={{ fontSize: 11 }}>买入触发</Text>
                <div style={{ marginTop: 2 }}>
                  <Text style={{ fontWeight: 700, color: "#52c41a", fontSize: 18 }}>
                    {trade.buy_trigger_val != null ? trade.buy_trigger_val.toFixed(4) : "—"}
                  </Text>
                  <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                    {subName} {OP_SYMBOL[params.buy_op] ?? params.buy_op} {params.buy_threshold} ✓
                  </Text>
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
                    <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                      {subName} {OP_SYMBOL[params.sell_op] ?? params.sell_op} {params.sell_threshold} ✓
                    </Text>
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
          {/* K 线 + 指标图 */}
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

// ── 回测详情 Drawer ───────────────────────────────────────────────────────────

interface RecordDetailDrawerProps {
  open: boolean;
  onClose: () => void;
  record: BacktestRecordItem | null;       // 列表摘要（用于 Drawer 标题和 K 线验证参数）
  detail: BacktestRecordDetail | null;     // 完整结果（含资金曲线和交易记录）
  loading: boolean;
}

function RecordDetailDrawer({ open, onClose, record, detail, loading }: RecordDetailDrawerProps) {
  const result       = detail?.result ?? null;
  const chartRef     = useRef<HTMLDivElement>(null);
  const chartInst    = useRef<echarts.ECharts | null>(null);
  const scrollYRef   = useRef(0);
  const isMobile = useIsMobile();
  const [selectedTrade, setSelectedTrade]   = useState<BacktestTradeRow | null>(null);
  const [tradeDrawerOpen, setTradeDrawerOpen] = useState(false);

  // 资金曲线渲染（含基准叠加）
  useEffect(() => {
    if (!result || !chartRef.current) return;
    if (!chartInst.current) {
      chartInst.current = echarts.init(chartRef.current);
    }
    const chart    = chartInst.current;
    const dates    = result.equity_curve.map((pt) => pt.date);
    const equities  = result.equity_curve.map((pt) => pt.equity);
    const drawdowns = result.equity_curve.map((pt) => pt.drawdown_pct);
    const benchEquities = (result.benchmark_curve ?? []).map((pt) => pt.equity);
    const hasBench = benchEquities.length > 0 && benchEquities.length === equities.length;
    const benchLabel = result.benchmark_index ? `基准(${result.benchmark_index})` : "基准";

    const legendData = ["总权益"];
    if (hasBench) legendData.push(benchLabel);
    legendData.push("回撤%");

    const series: echarts.SeriesOption[] = [
      {
        name: "总权益", type: "line", xAxisIndex: 0, yAxisIndex: 0,
        data: equities, smooth: false,
        lineStyle: { color: "#1677ff", width: 2 }, itemStyle: { color: "#1677ff" }, symbol: "none",
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(22,119,255,0.25)" },
          { offset: 1, color: "rgba(22,119,255,0.02)" },
        ]) },
      },
    ];
    if (hasBench) {
      series.push({
        name: benchLabel, type: "line", xAxisIndex: 0, yAxisIndex: 0,
        data: benchEquities, smooth: false,
        lineStyle: { color: "#faad14", width: 1.5, type: "dashed" },
        itemStyle: { color: "#faad14" }, symbol: "none",
      });
    }
    series.push({
      name: "回撤%", type: "line", xAxisIndex: 1, yAxisIndex: 1,
      data: drawdowns, smooth: false,
      lineStyle: { color: FALL_COLOR, width: 1.5 }, itemStyle: { color: FALL_COLOR }, symbol: "none",
      areaStyle: { color: "rgba(255,77,79,0.12)" },
    });

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: "#1f1f1f", borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        formatter: (params: echarts.TooltipComponentFormatterCallbackParams) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const date = String((params[0] as any).axisValue ?? (params[0] as any).name ?? "");
          const lines = params.map((p) => {
            const name = p.seriesName ?? "";
            const val  = typeof p.value === "number" ? p.value : Number(p.value);
            const fmt  = name === "回撤%" ? `${val.toFixed(2)}%` : `¥${fmtMoney(val)}`;
            return `<span style="display:inline-block;margin-right:4px;border-radius:10px;width:8px;height:8px;background:${p.color}"></span>${name}: ${fmt}`;
          });
          return `${date}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: { data: legendData, top: 8, textStyle: { color: "#d9d9d9", fontSize: 12 } },
      grid: [
        { left: 70, right: 20, top: 48, bottom: 120 },
        { left: 70, right: 20, top: "68%", bottom: 40 },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#333" } } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      ],
      yAxis: [
        { type: "value", gridIndex: 0, axisLabel: { color: "#8c8c8c", formatter: (v: number) => `¥${(v / 10000).toFixed(0)}万` }, splitLine: { lineStyle: { color: "#222" } } },
        { type: "value", gridIndex: 1, axisLabel: { color: "#8c8c8c", formatter: (v: number) => `${v.toFixed(1)}%` }, splitLine: { lineStyle: { color: "#222" } } },
      ],
      series,
    });

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [result]);

  // 关闭时销毁图表
  useEffect(() => {
    if (!open && chartInst.current) {
      chartInst.current.dispose();
      chartInst.current = null;
    }
  }, [open]);

  // 从 record 摘要组装 K 线验证图所需的参数
  const tradeParams = record ? {
    buy_op: record.buy_op, buy_threshold: record.buy_threshold,
    sell_op: record.sell_op, sell_threshold: record.sell_threshold,
    user_indicator_id: record.user_indicator_id ?? 0,
    sub_key: record.sub_key ?? "",
  } : null;

  const retColor = result ? pnlColor(result.total_return_pct) : undefined;

  const tradeColumns: ColumnsType<BacktestTradeRow> = [
    { title: "代码", dataIndex: "ts_code", width: 110, render: (v: string) => <Link to={`/?ts_code=${encodeURIComponent(v)}`}>{v}</Link> },
    { title: "名称", dataIndex: "name", width: 90, ellipsis: true },
    { title: "买入日", dataIndex: "buy_date", width: 100 },
    { title: "买入价", dataIndex: "buy_price", width: 80, align: "right" as const, render: (v: number) => v.toFixed(3) },
    {
      title: "卖出日", dataIndex: "sell_date", width: 100,
      render: (v: string | null) => v == null ? <Tag color="orange">持有中</Tag> : v,
    },
    { title: "卖出价", dataIndex: "sell_price", width: 80, align: "right" as const, render: (v: number | null) => v == null ? "—" : v.toFixed(3) },
    {
      title: "盈亏%", dataIndex: "pnl_pct", width: 85, align: "right" as const,
      render: (v: number | null) => v == null ? "—" : <span style={{ color: pnlColor(v) }}>{fmtPct(v)}</span>,
    },
    {
      title: "", key: "detail", width: 36,
      render: (_: unknown, row: BacktestTradeRow) => (
        <Tooltip title="K线验证图">
          <LineChartOutlined
            style={{ cursor: "pointer", color: "#4096ff", fontSize: 15 }}
            onClick={(e) => {
              e.stopPropagation();
              scrollYRef.current = window.scrollY;
              setSelectedTrade(row);
              setTradeDrawerOpen(true);
            }}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <Drawer
        title={
          record
            ? `${record.indicator_name || record.indicator_code} · ${record.start_date} ~ ${record.end_date}`
            : "回测详情"
        }
        open={open}
        onClose={onClose}
        width="80vw"
        styles={{ body: { padding: "16px 20px" } }}
      >
        {loading ? (
          <Skeleton active paragraph={{ rows: 10 }} />
        ) : result ? (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>

            {/* 口径与成本模型说明行（0.0.4-dev，老记录缺字段时跳过）*/}
            <Space size={6} wrap>
              <Tag color="blue">{result.adj_mode === "qfq" ? "前复权口径" : result.adj_mode || "未知口径"}</Tag>
              {result.execution_price && (
                <Tag color="cyan">
                  成交价：{result.execution_price === "next_open" ? "次日开盘" : "收盘价"}
                </Tag>
              )}
              {result.benchmark_index && (
                <Tag color="gold">基准：{result.benchmark_index}</Tag>
              )}
              {typeof result.commission_rate === "number" && (
                <Tag>佣金 {(result.commission_rate * 10000).toFixed(2)}‱</Tag>
              )}
              {typeof result.stamp_duty_rate === "number" && (
                <Tag>印花税 {(result.stamp_duty_rate * 1000).toFixed(2)}‰</Tag>
              )}
              {typeof result.slippage_bps === "number" && <Tag>滑点 {result.slippage_bps}bp</Tag>}
              {typeof result.lot_size === "number" && <Tag>整手 {result.lot_size} 股</Tag>}
            </Space>

            {/* 基准对比与成本（老记录无基准则隐藏该行）*/}
            {(result.benchmark_return_pct != null || result.alpha_pct != null || result.commission_cost_total) ? (
              <Row gutter={[16, 16]}>
                <Col xs={12} md={8}>
                  <Card size="small" styles={{ body: { padding: "12px 16px" } }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>基准收益</Text>
                    <div style={{ marginTop: 4 }}>
                      <Text
                        style={{
                          fontSize: 20,
                          fontWeight: 700,
                          color: pnlColor(result.benchmark_return_pct),
                        }}
                      >
                        {result.benchmark_return_pct == null ? "—" : fmtPct(result.benchmark_return_pct)}
                      </Text>
                      {result.benchmark_index && (
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 6 }}>
                          {result.benchmark_index}
                        </Text>
                      )}
                    </div>
                  </Card>
                </Col>
                <Col xs={12} md={8}>
                  <Card size="small" styles={{ body: { padding: "12px 16px" } }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>α（策略 - 基准）</Text>
                    <div style={{ marginTop: 4 }}>
                      <Text
                        style={{
                          fontSize: 20,
                          fontWeight: 700,
                          color: pnlColor(result.alpha_pct),
                        }}
                      >
                        {result.alpha_pct == null ? "—" : fmtPct(result.alpha_pct)}
                      </Text>
                    </div>
                  </Card>
                </Col>
                <Col xs={24} md={8}>
                  <Card size="small" styles={{ body: { padding: "12px 16px" } }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>交易成本合计</Text>
                    <div style={{ marginTop: 4 }}>
                      <Text style={{ fontSize: 20, fontWeight: 700, color: "#8c8c8c" }}>
                        ¥{fmtMoney(result.commission_cost_total ?? 0)}
                      </Text>
                    </div>
                  </Card>
                </Col>
              </Row>
            ) : null}

            {/* 绩效总览三格卡片 */}
            <Row gutter={[16, 16]}>
              {/* 核心收益 */}
              <Col xs={24} md={8}>
                <Card size="small" styles={{ body: { padding: "16px 20px" } }}>
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
                          <div style={{ fontWeight: 600, color: pnlColor(result.annualized_return) }}>{fmtPct(result.annualized_return)}</div>
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
                <Card size="small" styles={{ body: { padding: "16px 20px" } }}>
                  <Space align="start">
                    <FallOutlined style={{ fontSize: 28, color: FALL_COLOR, marginTop: 4 }} />
                    <div style={{ width: "100%" }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>风险控制</Text>
                      <Row gutter={16} style={{ marginTop: 8 }}>
                        <Col span={12}>
                          <MetricCard title="最大回撤" value={result.max_drawdown_pct} suffix="%" color={FALL_COLOR} hint="从历史最高点到最低点的最大跌幅，衡量极端风险" />
                        </Col>
                        <Col span={12}>
                          <MetricCard title="卡玛比率" value={result.calmar_ratio} hint="年化收益 / |最大回撤|，越高说明单位风险获取的收益越多" color={result.calmar_ratio != null && result.calmar_ratio > 1 ? RISE_COLOR : undefined} />
                        </Col>
                      </Row>
                      <Row gutter={16} style={{ marginTop: 12 }}>
                        <Col span={12}>
                          <MetricCard title="夏普比率" value={result.sharpe_ratio} hint="日超额收益均值 / 日收益标准差 × √252，衡量风险调整后收益" color={result.sharpe_ratio != null && result.sharpe_ratio > 1 ? RISE_COLOR : undefined} precision={3} />
                        </Col>
                        <Col span={12}>
                          <MetricCard title="盈亏比" value={result.profit_factor} hint="总盈利 / |总亏损|，> 1 表示总体盈利" color={result.profit_factor != null && result.profit_factor > 1 ? RISE_COLOR : FALL_COLOR} precision={3} />
                        </Col>
                      </Row>
                    </div>
                  </Space>
                </Card>
              </Col>

              {/* 交易统计 */}
              <Col xs={24} md={8}>
                <Card size="small" styles={{ body: { padding: "16px 20px" } }}>
                  <Space align="start">
                    <TrophyOutlined style={{ fontSize: 28, color: "#faad14", marginTop: 4 }} />
                    <div style={{ width: "100%" }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>交易统计</Text>
                      <Row gutter={16} style={{ marginTop: 8 }}>
                        <Col span={8}>
                          <Statistic title={<Text type="secondary" style={{ fontSize: 11 }}>总笔数</Text>} value={result.total_trades} valueStyle={{ fontSize: 20 }} />
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
                          <MetricCard title="平均持仓" value={result.avg_holding_days} suffix="天" hint="已平仓交易的平均持有天数" precision={1} />
                        </Col>
                      </Row>
                      <Divider style={{ margin: "10px 0" }} />
                      <Row gutter={8}>
                        <Col span={6}><Text type="secondary" style={{ fontSize: 11 }}>盈 {result.total_win} 笔</Text></Col>
                        <Col span={6}><Text type="secondary" style={{ fontSize: 11 }}>亏 {result.total_loss} 笔</Text></Col>
                        <Col span={6}><Text style={{ fontSize: 11, color: RISE_COLOR }}>均盈 {fmtPct(result.avg_win_pct)}</Text></Col>
                        <Col span={6}><Text style={{ fontSize: 11, color: FALL_COLOR }}>均亏 {result.avg_loss_pct != null ? fmtPct(-result.avg_loss_pct) : "—"}</Text></Col>
                      </Row>
                    </div>
                  </Space>
                </Card>
              </Col>
            </Row>

            {/* 资金曲线（总权益 + 回撤率双轴） */}
            <Card title={<Space><LineChartOutlined /><span>资金曲线</span></Space>}>
              <div ref={chartRef} style={{ width: "100%", height: isMobile ? 260 : 400 }} />
            </Card>

            {/* 交易记录明细 */}
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
                pagination={{ pageSize: 50, showSizeChanger: true }}
                scroll={{ x: "max-content" }}
                onRow={(row) => ({
                  style: { cursor: "pointer" },
                  onClick: () => { scrollYRef.current = window.scrollY; setSelectedTrade(row); setTradeDrawerOpen(true); },
                })}
              />
            </Card>
          </Space>
        ) : (
          <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
            <Text type="secondary">回测结果数据不可用</Text>
          </div>
        )}
      </Drawer>

      {/* K 线验证子 Drawer（嵌套在回测详情 Drawer 内） */}
      <TradeDetailDrawer
        open={tradeDrawerOpen}
        onClose={() => setTradeDrawerOpen(false)}
        onAfterClose={() => requestAnimationFrame(() => window.scrollTo(0, scrollYRef.current))}
        trade={selectedTrade}
        params={tradeParams}
        startDate={record?.start_date ?? ""}
        endDate={record?.end_date ?? ""}
      />
    </>
  );
}

// ── 回测对比 Drawer ───────────────────────────────────────────────────────────

/**
 * 每条回测曲线使用固定调色板着色（最多支持 4 条同时对比）
 * 颜色选择：蓝（主策略）、绿（对照）、橙（候选）、紫（辅助）
 */
const COMPARE_COLORS = ["#1677ff", "#52c41a", "#faad14", "#9254de"] as const;

interface CompareDrawerProps {
  open: boolean;
  onClose: () => void;
  records: BacktestRecordItem[];   // 2–4 条选中的记录
}

/**
 * CompareDrawer：将多条回测的资金曲线归一化为「累计收益率 %」后叠加对比。
 *
 * 归一化方式：pct = (equity / initial_capital − 1) × 100
 * 这样不同初始资金的策略也能在同一张图上直接比较涨幅而非绝对金额。
 */
function CompareDrawer({ open, onClose, records }: CompareDrawerProps) {
  const chartRef  = useRef<HTMLDivElement>(null);
  const chartInst = useRef<echarts.ECharts | null>(null);
  const [details, setDetails] = useState<(BacktestRecordDetail | null)[]>([]);
  const [loading, setLoading] = useState(false);

  // 每次打开时并发加载所有选中记录的详情（含资金曲线数据）
  useEffect(() => {
    if (!open || records.length === 0) return;
    setLoading(true);
    setDetails([]);
    Promise.all(
      records.map((r) => fetchBacktestRecordDetail(r.id).catch(() => null)),
    )
      .then(setDetails)
      .finally(() => setLoading(false));
  }, [open, records]);

  // 详情加载完成后绘制对比曲线
  useEffect(() => {
    if (!details.length || details.every((d) => !d?.result) || !chartRef.current) return;
    if (!chartInst.current) {
      chartInst.current = echarts.init(chartRef.current);
    }
    const chart = chartInst.current;

    // 为每条记录构建归一化收益率时间序列，用 [date_string, pct_value] 格式
    const series = records
      .map((rec, i) => {
        const d = details[i];
        if (!d?.result?.equity_curve?.length) return null;
        const ic = d.result.initial_capital;
        const label = `${rec.indicator_name || rec.indicator_code} (${fmtPct(d.result.total_return_pct)})`;
        return {
          name: label,
          type: "line" as const,
          data: d.result.equity_curve.map((pt) => [
            pt.date,
            +((pt.equity / ic - 1) * 100).toFixed(3),
          ]),
          lineStyle: { color: COMPARE_COLORS[i % COMPARE_COLORS.length], width: 2 },
          itemStyle: { color: COMPARE_COLORS[i % COMPARE_COLORS.length] },
          symbol: "none",
        };
      })
      .filter(Boolean);

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      legend: {
        data: series.map((s) => s!.name),
        top: 8,
        textStyle: { color: "#d9d9d9", fontSize: 11 },
        type: "scroll",
      },
      grid: { left: 64, right: 20, top: 48, bottom: 60 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          if (!Array.isArray(params)) return "";
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const date = String((params[0] as any)?.axisValue ?? "");
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const lines = params.map((p: any) => {
            const val = Array.isArray(p.value) ? Number(p.value[1]) : Number(p.value);
            return `<span style="display:inline-block;margin-right:4px;border-radius:10px;width:8px;height:8px;background:${p.color}"></span>${p.seriesName}: ${val > 0 ? "+" : ""}${val.toFixed(2)}%`;
          });
          return `${date}<br/>${lines.join("<br/>")}`;
        },
      },
      xAxis: {
        type: "time",                   // 时间轴：允许各曲线日期范围不同
        axisLabel: { color: "#8c8c8c", fontSize: 10 },
        axisLine: { lineStyle: { color: "#333" } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        name: "累计收益率 %",
        nameTextStyle: { color: "#8c8c8c", fontSize: 11 },
        axisLabel: { color: "#8c8c8c", formatter: (v: number) => `${v > 0 ? "+" : ""}${v}%` },
        splitLine: { lineStyle: { color: "#1e1e1e" } },
        // Y=0 基准线：零收益分界
        markLine: {
          silent: true,
          symbol: ["none", "none"],
          data: [{ yAxis: 0, lineStyle: { color: "#444", type: "dashed", width: 1 } }],
        },
      },
      dataZoom: [
        { type: "inside", start: 0, end: 100 },
        { type: "slider", bottom: 4, height: 18,
          textStyle: { color: "#8c8c8c", fontSize: 10 },
          handleStyle: { color: "#555" },
          fillerColor: "rgba(80,80,80,0.2)" },
      ],
      series,
    }, true);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [details, records]);

  // 关闭时销毁图表实例
  useEffect(() => {
    if (!open && chartInst.current) {
      chartInst.current.dispose();
      chartInst.current = null;
    }
  }, [open]);

  // 指标对比表格列（每条记录一行，指标为列）
  type MetricRow = { key: number; rec: BacktestRecordItem; result: BacktestRecordDetail["result"] };
  const compareRows: MetricRow[] = records.map((rec, i) => ({
    key: rec.id,
    rec,
    result: details[i]?.result ?? null,
  }));

  const compareColumns: ColumnsType<MetricRow> = [
    {
      title: "策略",
      key: "label",
      width: 200,
      render: (_: unknown, row: MetricRow, idx: number) => (
        <Space size={4}>
          <span style={{ width: 10, height: 10, borderRadius: "50%",
            background: COMPARE_COLORS[idx % COMPARE_COLORS.length],
            display: "inline-block" }} />
          <span style={{ fontSize: 12 }}>
            {row.rec.indicator_name || row.rec.indicator_code}
          </span>
        </Space>
      ),
    },
    {
      title: "总收益%", key: "ret", width: 90, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result
        ? <span style={{ color: pnlColor(row.result.total_return_pct), fontWeight: 600 }}>
            {fmtPct(row.result.total_return_pct)}
          </span>
        : "—",
    },
    {
      title: "年化%", key: "ann", width: 80, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result
        ? <span style={{ color: pnlColor(row.result.annualized_return) }}>
            {fmtPct(row.result.annualized_return)}
          </span>
        : "—",
    },
    {
      title: "最大回撤", key: "dd", width: 85, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result
        ? <span style={{ color: FALL_COLOR }}>{row.result.max_drawdown_pct.toFixed(2)}%</span>
        : "—",
    },
    {
      title: "夏普", key: "sharpe", width: 72, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result ? (row.result.sharpe_ratio?.toFixed(3) ?? "—") : "—",
    },
    {
      title: "胜率", key: "wr", width: 65, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result?.win_rate != null
        ? `${row.result.win_rate.toFixed(1)}%`
        : "—",
    },
    {
      title: "交易数", key: "trades", width: 65, align: "right" as const,
      render: (_: unknown, row: MetricRow) => row.result?.total_trades ?? "—",
    },
    {
      title: "时间范围", key: "range", width: 180,
      render: (_: unknown, row: MetricRow) => `${row.rec.start_date} ~ ${row.rec.end_date}`,
    },
  ];

  return (
    <Drawer
      title={
        <Space>
          <BarChartOutlined />
          <span>回测对比</span>
          <Tag color="blue">{records.length} 条</Tag>
        </Space>
      }
      open={open}
      onClose={onClose}
      width="85vw"
      styles={{ body: { padding: "16px 20px" } }}
    >
      {loading ? (
        <Skeleton active paragraph={{ rows: 12 }} />
      ) : (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          {/* 归一化收益率曲线对比图 */}
          <Card
            title="累计收益率曲线对比"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                各策略以初始资金归一，Y 轴为百分比收益率
              </Typography.Text>
            }
          >
            <div ref={chartRef} style={{ width: "100%", height: 400 }} />
          </Card>

          {/* 关键指标横向对比表 */}
          <Card title="关键指标对比">
            <Table<MetricRow>
              rowKey="key"
              size="small"
              columns={compareColumns}
              dataSource={compareRows}
              pagination={false}
              scroll={{ x: 800 }}
            />
          </Card>
        </Space>
      )}
    </Drawer>
  );
}

// ── 主页面 ─────────────────────────────────────────────────────────────────────

export default function BacktestHistoryPage() {
  const [records, setRecords]           = useState<BacktestRecordItem[]>([]);
  const [loading, setLoading]           = useState(false);
  const [detailRecord, setDetailRecord] = useState<BacktestRecordItem | null>(null);
  const [detail, setDetail]             = useState<BacktestRecordDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailOpen, setDetailOpen]     = useState(false);

  // 多选对比状态
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);

  // 从 records 中找出选中行对应的完整对象，传给 CompareDrawer
  const selectedRecords = useMemo(
    () => records.filter((r) => selectedRowKeys.includes(r.id)),
    [records, selectedRowKeys],
  );

  const loadRecords = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await fetchBacktestRecords({ page_size: 100 });
      setRecords(rows);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadRecords(); }, [loadRecords]);

  /** 点击某条记录：先显示 Drawer 和骨架屏，再异步加载完整详情 */
  const openDetail = useCallback(async (rec: BacktestRecordItem) => {
    setDetailRecord(rec);
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      const d = await fetchBacktestRecordDetail(rec.id);
      setDetail(d);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleDelete = useCallback(async (id: number) => {
    try {
      await deleteBacktestRecord(id);
      message.success("已删除");
      void loadRecords();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    }
  }, [loadRecords]);

  // 记录列表的表格列定义
  const columns: ColumnsType<BacktestRecordItem> = [
    {
      title: "执行时间",
      dataIndex: "created_at",
      width: 135,
      render: (v: string) => v ? dayjs(v).format("MM-DD HH:mm") : "—",
    },
    {
      title: "时间范围",
      key: "range",
      width: 180,
      render: (_: unknown, r: BacktestRecordItem) => `${r.start_date} ~ ${r.end_date}`,
    },
    {
      title: "指标 / 子线",
      key: "indicator",
      ellipsis: true,
      render: (_: unknown, r: BacktestRecordItem) => (
        <Space direction="vertical" size={0}>
          <span>{r.indicator_name || r.indicator_code}</span>
          {r.sub_key ? <Tag style={{ fontSize: 11 }}>{r.sub_key}</Tag> : null}
        </Space>
      ),
    },
    {
      title: "买入 / 卖出",
      key: "conditions",
      width: 160,
      render: (_: unknown, r: BacktestRecordItem) => (
        <Space direction="vertical" size={0}>
          <Text style={{ fontSize: 12 }}>
            <span style={{ color: RISE_COLOR, fontWeight: 600 }}>买</span>{" "}
            {OP_SYMBOL[r.buy_op] ?? r.buy_op} {r.buy_threshold}
          </Text>
          <Text style={{ fontSize: 12 }}>
            <span style={{ color: FALL_COLOR, fontWeight: 600 }}>卖</span>{" "}
            {OP_SYMBOL[r.sell_op] ?? r.sell_op} {r.sell_threshold}
          </Text>
        </Space>
      ),
    },
    {
      title: "总收益%",
      dataIndex: "total_return_pct",
      width: 100,
      align: "right" as const,
      sorter: (a, b) => a.total_return_pct - b.total_return_pct,
      render: (v: number) => (
        <span style={{ color: pnlColor(v), fontWeight: 600 }}>
          {v > 0 ? "+" : ""}{v.toFixed(2)}%
        </span>
      ),
    },
    {
      title: "α%",
      dataIndex: "alpha_pct",
      width: 85,
      align: "right" as const,
      sorter: (a, b) => (a.alpha_pct ?? 0) - (b.alpha_pct ?? 0),
      render: (v: number | null) =>
        v == null ? "—" : (
          <span style={{ color: pnlColor(v), fontWeight: 600 }}>
            {v > 0 ? "+" : ""}{v.toFixed(2)}%
          </span>
        ),
    },
    {
      title: "最大回撤",
      dataIndex: "max_drawdown_pct",
      width: 95,
      align: "right" as const,
      sorter: (a, b) => a.max_drawdown_pct - b.max_drawdown_pct,
      render: (v: number) => <span style={{ color: FALL_COLOR }}>{v.toFixed(2)}%</span>,
    },
    {
      title: "年化%",
      dataIndex: "annualized_return",
      width: 85,
      align: "right" as const,
      sorter: (a, b) => (a.annualized_return ?? 0) - (b.annualized_return ?? 0),
      render: (v: number | null) =>
        v == null ? "—" : (
          <span style={{ color: pnlColor(v) }}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>
        ),
    },
    {
      title: "胜率%",
      dataIndex: "win_rate",
      width: 75,
      align: "right" as const,
      render: (v: number | null) => v == null ? "—" : `${v.toFixed(1)}%`,
    },
    {
      title: "交易数",
      dataIndex: "total_trades",
      width: 70,
      align: "right" as const,
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      render: (_: unknown, r: BacktestRecordItem) => (
        <Space>
          <Button size="small" type="link" onClick={(e) => { e.stopPropagation(); void openDetail(r); }}>
            查看
          </Button>
          <Popconfirm
            title="确认删除这条回测记录？"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={(e) => { e?.stopPropagation(); void handleDelete(r.id); }}
          >
            <Button size="small" type="link" danger onClick={(e) => e.stopPropagation()}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1400 }}>
      <div>
        <Title level={4} style={{ margin: 0 }}>回测历史记录</Title>
        <Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
          每次在「开始回测」页面执行回测后，结果会自动保存到此处。点击任意记录可查看完整的资金曲线和交易明细。
        </Paragraph>
      </div>

      <Card
        extra={
          // 勾选 2–4 条记录时显示对比按钮；超过 4 条时给出提示
          selectedRowKeys.length >= 2 && selectedRowKeys.length <= 4 ? (
            <Button
              type="primary"
              size="small"
              icon={<BarChartOutlined />}
              onClick={() => setCompareOpen(true)}
            >
              对比所选 {selectedRowKeys.length} 条
            </Button>
          ) : selectedRowKeys.length > 4 ? (
            <Typography.Text type="warning" style={{ fontSize: 12 }}>
              最多同时对比 4 条，请减少勾选
            </Typography.Text>
          ) : selectedRowKeys.length === 1 ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              再勾选 1–3 条即可对比
            </Typography.Text>
          ) : null
        }
      >
        <Table<BacktestRecordItem>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={records}
          loading={loading}
          rowClassName={zebraRowClass}
          // 多选行：勾选后显示对比按钮；最大允许选 4 条
          rowSelection={{
            selectedRowKeys,
            onChange: setSelectedRowKeys,
            // 点击 checkbox 不触发行的 onClick（AntD 默认行为已隔离）
          }}
          pagination={{
            pageSize: 20,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条记录`,
          }}
          scroll={{ x: "max-content" }}
          locale={{ emptyText: "暂无回测记录，请先在「开始回测」页面执行一次回测" }}
          onRow={(r) => ({
            style: { cursor: "pointer" },
            // 点击行本体（非 checkbox）打开详情
            onClick: () => void openDetail(r),
          })}
        />
      </Card>

      {/* 回测详情 Drawer（含资金曲线、交易记录、K线验证子 Drawer） */}
      <RecordDetailDrawer
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        record={detailRecord}
        detail={detail}
        loading={detailLoading}
      />

      {/* 多策略对比 Drawer */}
      <CompareDrawer
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        records={selectedRecords}
      />
    </Space>
  );
}
