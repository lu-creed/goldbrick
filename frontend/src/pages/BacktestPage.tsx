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
  Form,
  InputNumber,
  Row,
  Select,
  Skeleton,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  InfoCircleOutlined,
  LineChartOutlined,
  RiseOutlined,
  FallOutlined,
  TrophyOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchCustomIndicators,
  getApiErrorMessage,
  runBacktest,
  type BacktestRunOut,
  type BacktestTradeRow,
  type UserIndicatorOut,
} from "../api/client";
import { ECHARTS_BASE_OPTION, FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";

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

export default function BacktestPage() {
  const [form] = Form.useForm();

  const [indicators, setIndicators] = useState<UserIndicatorOut[]>([]);
  const [loadingInd, setLoadingInd] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestRunOut | null>(null);

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

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
  ];

  const onRun = async () => {
    try {
      const v = await form.validateFields();
      const [start, end] = v.date_range as [Dayjs, Dayjs];
      setRunning(true);
      setResult(null);
      if (chartInstance.current) chartInstance.current.clear();
      try {
        const out = await runBacktest({
          start_date: start.format("YYYY-MM-DD"),
          end_date: end.format("YYYY-MM-DD"),
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
      <Card title="回测配置" styles={{ body: { paddingBottom: 8 } }}>
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
          </Form>
        )}
      </Card>

      {/* 结果区 */}
      {result && (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>

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
            <div ref={chartRef} style={{ width: "100%", height: 440 }} />
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
              scroll={{ x: 700 }}
            />
          </Card>

        </Space>
      )}
    </Space>
  );
}
