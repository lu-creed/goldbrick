import {
  Alert,
  Button,
  Card,
  Checkbox,
  ConfigProvider,
  DatePicker,
  Form,
  InputNumber,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import * as echarts from "echarts";
import { type Dayjs } from "dayjs";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  type AdjType,
  type ConditionBuyDailyPoint,
  type ConditionBuyRequest,
  type IndicatorRef,
  fetchSymbols,
  getApiErrorMessage,
  runConditionBuyBacktest,
} from "../api/client";

// ─── 指标子线映射 ───────────────────────────────────────────────
const INDICATOR_SUB_MAP: Record<string, string[]> = {
  MA:         ["MA5", "MA10", "MA20", "MA30", "MA60"],
  EXPMA:      ["EXPMA12", "EXPMA26"],
  BOLL:       ["UPPER", "MID", "LOWER"],
  MACD:       ["DIF", "DEA", "MACD柱"],
  KDJ:        ["K", "D", "J"],
  个股数据:   ["close", "open", "high", "low", "turnover_rate", "volume"],
};

const INDICATOR_OPTIONS = Object.keys(INDICATOR_SUB_MAP).map((k) => ({ value: k, label: k }));

/** 控件最大宽度（量化表单密度：避免输入框过长） */
const INPUT_MAX = 240;
const RANGE_MAX = 400;

/** 分区卡片：白底 + 细边框 + 轻阴影 + 左侧蓝色指示条；标题下浅色分割线 */
function SectionCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-100 bg-white shadow-sm">
      <header className="px-5 pt-3 pb-0">
        <div className="flex items-center gap-2 pb-2.5">
          <span className="h-4 w-1 shrink-0 rounded-full bg-blue-600" aria-hidden />
          <h2 className="text-sm font-semibold tracking-tight text-slate-800">{title}</h2>
        </div>
        <div className="h-px w-full bg-slate-100" aria-hidden />
      </header>
      <div className="p-5 pt-4">{children}</div>
    </section>
  );
}

// ─── IndicatorRef 选择器 ────────────────────────────────────────
type IndicatorRefValue = { kind: "number"; value?: number } | { kind: "indicator"; indicator?: string; sub_name?: string };

function IndicatorRefSelector({
  value,
  onChange,
}: {
  value?: IndicatorRefValue;
  onChange?: (v: IndicatorRefValue) => void;
}) {
  const kind = value?.kind ?? "indicator";
  const indicator = value?.kind === "indicator" ? value.indicator : undefined;
  const subs = indicator ? (INDICATOR_SUB_MAP[indicator] ?? []) : [];

  return (
    <Space size={4} wrap className="w-full">
      <Select
        className="min-w-[88px] max-w-[104px]"
        style={{ width: 90 }}
        value={kind}
        onChange={(k) => onChange?.({ kind: k as "number" | "indicator" })}
        options={[
          { value: "indicator", label: "指标" },
          { value: "number", label: "数字" },
        ]}
      />
      {kind === "indicator" ? (
        <>
          <Select
            className="min-w-[88px] max-w-[120px]"
            style={{ width: 90 }}
            placeholder="选指标"
            value={indicator}
            onChange={(v) => onChange?.({ kind: "indicator", indicator: v, sub_name: undefined })}
            options={INDICATOR_OPTIONS}
          />
          <Select
            className="min-w-[100px] max-w-[132px]"
            style={{ width: 110 }}
            placeholder="子指标"
            value={value?.kind === "indicator" ? value.sub_name : undefined}
            onChange={(v) => onChange?.({ kind: "indicator", indicator, sub_name: v })}
            options={subs.map((s) => ({ value: s, label: s }))}
            disabled={!indicator}
          />
        </>
      ) : (
        <InputNumber
          className="w-full max-w-[120px]"
          style={{ width: 100 }}
          placeholder="输入数字"
          value={value?.kind === "number" ? value.value : undefined}
          onChange={(v) => onChange?.({ kind: "number", value: v ?? undefined })}
        />
      )}
    </Space>
  );
}

// ─── 子指标单选（用于买入价格 / 指标价）─────────────────────────
function SubIndicatorSelector({
  value,
  onChange,
}: {
  value?: string;
  onChange?: (v: string) => void;
}) {
  const [indicator, setIndicator] = useState<string | undefined>(() => {
    if (!value) return undefined;
    for (const [k, subs] of Object.entries(INDICATOR_SUB_MAP)) {
      if (subs.includes(value)) return k;
    }
    return undefined;
  });
  const subs = indicator ? (INDICATOR_SUB_MAP[indicator] ?? []) : [];

  return (
    <Space size={4} wrap className="w-full">
      <Select
        className="min-w-[96px] max-w-[120px]"
        style={{ width: 100 }}
        placeholder="选指标"
        value={indicator}
        onChange={(v) => { setIndicator(v); onChange?.(undefined!); }}
        options={INDICATOR_OPTIONS}
      />
      <Select
        className="min-w-[100px] max-w-[132px]"
        style={{ width: 110 }}
        placeholder="子指标"
        value={value}
        onChange={onChange}
        options={subs.map((s) => ({ value: s, label: s }))}
        disabled={!indicator}
      />
    </Space>
  );
}

// ─── 工具：将内部表单值转换为 IndicatorRef ──────────────────────
function toIndicatorRef(v: IndicatorRefValue | undefined): IndicatorRef | undefined {
  if (!v) return undefined;
  if (v.kind === "number") return v.value != null ? { kind: "number", value: v.value } : undefined;
  if (v.sub_name) return { kind: "indicator", sub_name: v.sub_name };
  return undefined;
}

// ─── 主页面 ─────────────────────────────────────────────────────
type FormVals = {
  ts_code: string;
  range: [Dayjs, Dayjs];
  initial_cash: number;
  adj: AdjType;
  time_offset: number;
  condition_type: "price" | "indicator";
  price_value?: number;
  ind_left?: IndicatorRefValue;
  ind_op?: "gt" | "eq" | "lt";
  ind_right?: IndicatorRefValue;
  buy_price_type: "fixed" | "indicator";
  buy_price_fixed?: number;
  buy_price_sub?: string;
  buy_qty_type: "fixed" | "ratio";
  buy_qty_fixed?: number;
  buy_qty_ratio?: number;
  enable_sell: boolean;
  sell_target_price?: number;
  sell_target_return?: number;
  sell_target_date?: Dayjs;
  sell_logic: "or" | "and";
};

// sessionStorage key
const SS_ROWS = "backtest_rows";
const SS_RESP = "backtest_resp";

function ssLoad<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch { return null; }
}
function ssSave(key: string, val: unknown) {
  try { sessionStorage.setItem(key, JSON.stringify(val)); } catch { /* 配额满时静默忽略 */ }
}

export default function BacktestPage() {
  const [form] = Form.useForm<FormVals>();
  const [symbols, setSymbols] = useState<{ label: string; value: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  // 从 sessionStorage 恢复上次结果，避免切换页签后丢失
  const [rows, setRows] = useState<ConditionBuyDailyPoint[]>(() => ssLoad<ConditionBuyDailyPoint[]>(SS_ROWS) ?? []);
  const [resp, setResp] = useState<{ buy_count: number; max_drawdown: number; sell_date?: string | null; sell_price?: number | null; sell_reason?: string | null } | null>(() => ssLoad(SS_RESP));
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartIns = useRef<echarts.ECharts | null>(null);

  const condType = Form.useWatch("condition_type", form);
  const buyPriceType = Form.useWatch("buy_price_type", form);
  const buyQtyType = Form.useWatch("buy_qty_type", form);
  const enableSell = Form.useWatch("enable_sell", form);
  const timeOffset = Form.useWatch("time_offset", form);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        const s = await fetchSymbols();
        const opts = s.map((x) => ({ value: x.ts_code, label: x.name ? `${x.ts_code} ${x.name}` : x.ts_code }));
        setSymbols(opts);
        if (opts[0]) form.setFieldValue("ts_code", opts[0].value);
      } catch (e) {
        setError(getApiErrorMessage(e));
      } finally {
        setLoading(false);
      }
    })();
  }, [form]);

  // 买入点索引（用于图表标注）
  const buyDates = useMemo(() => {
    const s = new Set<string>();
    rows.forEach((r, i) => {
      if (i === 0) return;
      if (r.holding_qty > (rows[i - 1]?.holding_qty ?? 0)) s.add(r.trade_date);
    });
    return s;
  }, [rows]);

  // 绘图
  useEffect(() => {
    if (!chartRef.current || rows.length === 0) return;
    if (!chartIns.current) chartIns.current = echarts.init(chartRef.current);
    const ins = chartIns.current;
    const xData = rows.map((r) => r.trade_date);
    const totalData = rows.map((r) => +r.total_asset.toFixed(2));
    const stockData = rows.map((r) => +r.stock_value.toFixed(2));
    const cashData = rows.map((r) => +r.cash_value.toFixed(2));
    // 累计收益率转为百分比，保留两位小数
    const returnData = rows.map((r) => +(r.cum_return * 100).toFixed(2));

    const buyMarks = rows
      .filter((r) => buyDates.has(r.trade_date))
      .map((r) => ({ coord: [r.trade_date, r.total_asset] }));

    const sellMark = resp?.sell_date
      ? [{ coord: [resp.sell_date, rows.find((r) => r.trade_date === resp.sell_date)?.total_asset ?? 0] }]
      : [];

    ins.setOption({
      tooltip: {
        trigger: "axis",
        formatter: (params: any[]) => {
          const date = params[0]?.axisValue ?? "";
          const lines = params.map((p: any) => {
            const val = p.seriesName === "累计收益率"
              ? `${p.value}%`
              : `¥${(p.value as number).toLocaleString()}`;
            return `${p.marker}${p.seriesName}：${val}`;
          });
          return `${date}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: { data: ["总资产", "股票市值", "现金", "累计收益率"], top: 4 },
      grid: { left: 70, right: 70, top: 36, bottom: 40 },
      xAxis: { type: "category", data: xData, boundaryGap: false },
      yAxis: [
        { type: "value", scale: true, name: "金额(¥)", nameTextStyle: { color: "#999" }, axisLabel: { formatter: (v: number) => v >= 10000 ? `${(v / 10000).toFixed(1)}万` : String(v) } },
        { type: "value", scale: true, name: "收益率(%)", nameTextStyle: { color: "#fa8c16" }, position: "right", axisLabel: { formatter: (v: number) => `${v}%` }, splitLine: { show: false } },
      ],
      series: [
        {
          name: "总资产", type: "line", yAxisIndex: 0, data: totalData, smooth: true,
          lineStyle: { width: 2 },
          markPoint: {
            data: [
              ...buyMarks.map((m) => ({ ...m, name: "买入", symbol: "arrow", symbolSize: 12, itemStyle: { color: "#cf1322" } })),
              ...sellMark.map((m) => ({ ...m, name: "卖出", symbol: "arrow", symbolRotate: 180, symbolSize: 12, itemStyle: { color: "#389e0d" } })),
            ],
          },
        },
        { name: "股票市值", type: "line", yAxisIndex: 0, data: stockData, smooth: true, lineStyle: { type: "dashed" } },
        { name: "现金", type: "line", yAxisIndex: 0, data: cashData, smooth: true, lineStyle: { type: "dashed" } },
        {
          name: "累计收益率", type: "line", yAxisIndex: 1, data: returnData, smooth: true,
          lineStyle: { width: 2, color: "#fa8c16" },
          itemStyle: { color: "#fa8c16" },
          areaStyle: { color: "rgba(250,140,22,0.08)" },
          // 正负区分颜色
          markLine: { silent: true, lineStyle: { color: "#999", type: "dashed" }, data: [{ yAxis: 0 }] },
        },
      ],
    });
    ins.resize();
  }, [rows, buyDates, resp]);

  const onRun = async () => {
    const v = await form.validateFields();
    setRunning(true);
    setError(null);
    try {
      const buyPrice: ConditionBuyRequest["buy_price"] =
        v.buy_price_type === "fixed"
          ? { type: "fixed", fixed_price: v.buy_price_fixed ?? 0 }
          : { type: "indicator", sub_name: v.buy_price_sub ?? "" };

      const buyQty: ConditionBuyRequest["buy_qty"] =
        v.buy_qty_type === "fixed"
          ? { type: "fixed", fixed_qty: v.buy_qty_fixed ?? 100 }
          : { type: "ratio", ratio: (v.buy_qty_ratio ?? 10) / 100 };

      const timing: ConditionBuyRequest["buy_timing"] =
        v.condition_type === "price"
          ? { time_offset: -(v.time_offset ?? 0), condition_type: "price", price: v.price_value }
          : {
              time_offset: -(v.time_offset ?? 0),
              condition_type: "indicator",
              left: toIndicatorRef(v.ind_left),
              operator: v.ind_op,
              right: toIndicatorRef(v.ind_right),
            };

      const r = await runConditionBuyBacktest({
        ts_code: v.ts_code,
        start_date: v.range[0].format("YYYY-MM-DD"),
        end_date: v.range[1].format("YYYY-MM-DD"),
        initial_cash: v.initial_cash,
        adj: v.adj,
        buy_timing: timing,
        buy_price: buyPrice,
        buy_qty: buyQty,
        sell_target_price: v.enable_sell ? v.sell_target_price : undefined,
        sell_target_return: v.enable_sell ? v.sell_target_return : undefined,
        sell_target_date: v.enable_sell && v.sell_target_date ? v.sell_target_date.format("YYYY-MM-DD") : undefined,
        sell_logic: v.sell_logic,
      });
      setRows(r.daily);
      setResp(r);
      ssSave(SS_ROWS, r.daily);
      ssSave(SS_RESP, r);
      message.success(`回测完成，触发买入 ${r.buy_count} 次`);
    } catch (e) {
      setRows([]);
      setResp(null);
      setError(getApiErrorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  const columns: ColumnsType<ConditionBuyDailyPoint> = [
    { title: "日期", dataIndex: "trade_date", width: 110,
      render: (v: string) => buyDates.has(v) ? <Tag color="red">{v} 买入</Tag> : v },
    { title: "收盘价", dataIndex: "close", width: 90, render: (v: number) => v.toFixed(2) },
    { title: "持仓股数", dataIndex: "holding_qty", width: 90 },
    { title: "股票市值", dataIndex: "stock_value", width: 110, render: (v: number) => v.toFixed(2) },
    { title: "现金", dataIndex: "cash_value", width: 110, render: (v: number) => v.toFixed(2) },
    { title: "总资产", dataIndex: "total_asset", width: 110, render: (v: number) => v.toFixed(2) },
    { title: "当日盈亏", dataIndex: "daily_pnl", width: 100,
      render: (v: number) => <span style={{ color: v >= 0 ? "#cf1322" : "#389e0d" }}>{v >= 0 ? "+" : ""}{v.toFixed(2)}</span> },
    { title: "累计收益率", dataIndex: "cum_return", width: 110,
      render: (v: number) => <span style={{ color: v >= 0 ? "#cf1322" : "#389e0d" }}>{(v * 100).toFixed(2)}%</span> },
  ];

  return (
    <ConfigProvider
      theme={{
        token: {
          borderRadius: 8,
          colorBorder: "#f1f5f9",
          colorBorderSecondary: "#e2e8f0",
          colorPrimary: "#2563eb",
        },
      }}
    >
      <div className="-mx-6 min-h-[calc(100vh-112px)] bg-slate-50 px-6 py-6">
        <div className="mx-auto w-full max-w-6xl space-y-6">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <Typography.Title level={4} className="!mb-0">
              策略回测
            </Typography.Title>
            <span className="text-xs font-medium text-slate-400">量化回测工作台 · 参数卡片化</span>
          </div>

          {error ? <Alert type="error" showIcon message="回测失败" description={error} className="rounded-xl border-red-100" /> : null}

          <Spin spinning={loading}>
            <Form<FormVals>
              form={form}
              layout="vertical"
              colon={false}
              className="backtest-form space-y-6"
              initialValues={{
                initial_cash: 100000,
                adj: "none",
                time_offset: 0,
                condition_type: "price",
                buy_price_type: "fixed",
                buy_qty_type: "fixed",
                buy_qty_fixed: 100,
                sell_logic: "or",
                enable_sell: false,
              }}
            >
              <SectionCard title="基本参数">
                {/* 12 列栅格：第二行「区间=6、现金=3、复权=3」符合 2:1:1 */}
                <div className="grid grid-cols-12 gap-x-4 gap-y-2">
                  <Form.Item
                    className="backtest-grid-cell col-span-12 md:col-span-6 lg:col-span-4"
                    name="ts_code"
                    label="标的"
                    rules={[{ required: true, message: "请选择标的" }]}
                  >
                    <Select showSearch placeholder="搜索代码或名称" options={symbols} optionFilterProp="label" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                  </Form.Item>
                </div>
                <div className="mt-1 grid grid-cols-12 gap-x-4 gap-y-2">
                  <Form.Item
                    className="backtest-grid-cell col-span-12 lg:col-span-6"
                    name="range"
                    label="回测区间"
                    rules={[{ required: true, message: "请选择区间" }]}
                  >
                    <DatePicker.RangePicker className="w-full" style={{ width: "100%", maxWidth: RANGE_MAX }} />
                  </Form.Item>
                  <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-3" name="initial_cash" label="初始现金（元）">
                    <InputNumber min={0} precision={2} placeholder="如 100000" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                  </Form.Item>
                  <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-3" name="adj" label="复权方式">
                    <Select
                      placeholder="选择复权"
                      className="w-full"
                      style={{ width: "100%", maxWidth: INPUT_MAX }}
                      options={[
                        { value: "none", label: "不复权" },
                        { value: "qfq", label: "前复权" },
                        { value: "hfq", label: "后复权" },
                      ]}
                    />
                  </Form.Item>
                </div>
              </SectionCard>

              <SectionCard title="买入时机">
                <div className="grid grid-cols-12 gap-x-4 gap-y-2">
                  <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-4" name="time_offset" label="时间条件">
                    <Select
                      className="w-full"
                      style={{ width: "100%", maxWidth: INPUT_MAX }}
                      options={[
                        { value: 0, label: "当日 T" },
                        { value: 1, label: "T-N 交易日" },
                      ]}
                    />
                  </Form.Item>
                  {timeOffset > 0 ? (
                    <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-3" name="time_offset_n" label="回溯 N（交易日）">
                      <InputNumber
                        min={1}
                        max={20}
                        defaultValue={1}
                        placeholder="如 3"
                        className="w-full"
                        style={{ width: "100%", maxWidth: 160 }}
                        onChange={(v) => form.setFieldValue("time_offset", v ?? 1)}
                      />
                    </Form.Item>
                  ) : null}
                  <Form.Item className="backtest-grid-cell col-span-12 lg:col-span-8" name="condition_type" label="条件类型">
                    <Segmented
                      size="small"
                      block
                      className="ant-segmented-sm !rounded-lg"
                      options={[
                        { label: "价格满足", value: "price" },
                        { label: "指标满足", value: "indicator" },
                      ]}
                    />
                  </Form.Item>
                </div>

                {condType === "price" ? (
                  <div className="mt-3">
                    <Alert
                      type="info"
                      showIcon
                      message="条件说明"
                      description={
                        <span className="text-xs leading-relaxed text-slate-600">
                          当日最低价 &lt; 所填阈值 &lt; 当日最高价时，视为本交易日触发候选条件（最终以服务端撮合规则为准）。
                        </span>
                      }
                      className="rounded-lg border-blue-100 !bg-blue-50 !py-2 [&_.ant-alert-message]:text-xs [&_.ant-alert-description]:mt-1"
                    />
                    <Form.Item
                      className="backtest-grid-cell mb-0 mt-3 max-w-[260px]"
                      name="price_value"
                      label="价格阈值"
                      rules={[{ required: true, message: "必填" }]}
                      extra="单位：元；需落在当日高低价之间才判定为满足。"
                    >
                      <InputNumber min={0.01} precision={3} placeholder="必填" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                    </Form.Item>
                  </div>
                ) : null}

                {condType === "indicator" ? (
                  <div className="mt-3 rounded-lg border border-slate-100 bg-slate-50/60 p-4">
                    <div className="grid grid-cols-12 gap-x-4 gap-y-0 md:items-end">
                      <Form.Item className="backtest-grid-cell col-span-12 md:col-span-5 !mb-3 md:!mb-0" name="ind_left" label="左操作数">
                        <IndicatorRefSelector />
                      </Form.Item>
                      <Form.Item className="backtest-grid-cell col-span-12 md:col-span-2 !mb-3 md:!mb-0" name="ind_op" label="比较">
                        <Select
                          className="w-full"
                          style={{ width: "100%", maxWidth: INPUT_MAX }}
                          options={[
                            { value: "gt", label: ">" },
                            { value: "eq", label: "=" },
                            { value: "lt", label: "<" },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item className="backtest-grid-cell col-span-12 md:col-span-5 !mb-0" name="ind_right" label="右操作数">
                        <IndicatorRefSelector />
                      </Form.Item>
                    </div>
                  </div>
                ) : null}
              </SectionCard>

              <SectionCard title="买入价格与数量">
                <div className="grid grid-cols-12 gap-x-6 gap-y-2">
                  <div className="col-span-12 lg:col-span-6">
                    <Form.Item className="backtest-grid-cell" name="buy_price_type" label="成交价格">
                      <Segmented
                        size="small"
                        block
                        className="ant-segmented-sm !rounded-lg"
                        options={[
                          { label: "定价买入", value: "fixed" },
                          { label: "按指标价", value: "indicator" },
                        ]}
                      />
                    </Form.Item>
                    {buyPriceType === "fixed" ? (
                      <Form.Item className="backtest-grid-cell" name="buy_price_fixed" label="买入价（元）" rules={[{ required: true, message: "必填" }]}>
                        <InputNumber min={0.01} precision={3} placeholder="必填" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                      </Form.Item>
                    ) : null}
                    {buyPriceType === "indicator" ? (
                      <Form.Item className="backtest-grid-cell" name="buy_price_sub" label="价格取自子指标">
                        <SubIndicatorSelector />
                      </Form.Item>
                    ) : null}
                  </div>
                  <div className="col-span-12 lg:col-span-6">
                    <Form.Item className="backtest-grid-cell" name="buy_qty_type" label="买入数量">
                      <Segmented
                        size="small"
                        block
                        className="ant-segmented-sm !rounded-lg"
                        options={[
                          { label: "固定股数", value: "fixed" },
                          { label: "现金比例", value: "ratio" },
                        ]}
                      />
                    </Form.Item>
                    {buyQtyType === "fixed" ? (
                      <Form.Item className="backtest-grid-cell" name="buy_qty_fixed" label="股数">
                        <InputNumber min={100} step={100} precision={0} placeholder="如 100" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                      </Form.Item>
                    ) : null}
                    {buyQtyType === "ratio" ? (
                      <Form.Item className="backtest-grid-cell" name="buy_qty_ratio" label="可用现金比例（%）">
                        <InputNumber min={1} max={100} precision={0} placeholder="1–100" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} suffix="%" />
                      </Form.Item>
                    ) : null}
                  </div>
                </div>
              </SectionCard>

              <SectionCard title="卖出与风控">
                <Form.Item name="enable_sell" valuePropName="checked" className={enableSell ? "!mb-4" : "!mb-0"}>
                  <Checkbox>
                    <span className="text-sm font-medium text-slate-700">启用卖出条件</span>
                  </Checkbox>
                </Form.Item>
                {enableSell ? (
                  <div className="grid grid-cols-12 gap-x-4 gap-y-2">
                    <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-4" label="目标价" name="sell_target_price">
                      <InputNumber min={0.01} precision={3} placeholder="可选" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                    </Form.Item>
                    <Form.Item
                      className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-4"
                      label="目标收益率"
                      name="sell_target_return"
                      tooltip="小数，如 0.1 表示 10%"
                    >
                      <InputNumber min={-1} max={10} step={0.01} precision={4} placeholder="可选" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                    </Form.Item>
                    <Form.Item className="backtest-grid-cell col-span-12 sm:col-span-6 lg:col-span-4" label="目标日期" name="sell_target_date">
                      <DatePicker placeholder="可选" className="w-full" style={{ width: "100%", maxWidth: INPUT_MAX }} />
                    </Form.Item>
                    <Form.Item className="backtest-grid-cell col-span-12" label="条件关系" name="sell_logic">
                      <Segmented
                        size="small"
                        block
                        className="ant-segmented-sm !rounded-lg"
                        options={[
                          { label: "满足任一（OR）", value: "or" },
                          { label: "全部满足（AND）", value: "and" },
                        ]}
                      />
                    </Form.Item>
                  </div>
                ) : null}
              </SectionCard>

              <Button type="primary" size="large" loading={running} className="h-11 rounded-lg px-10 shadow-sm" onClick={() => void onRun()}>
                执行回测
              </Button>
            </Form>
          </Spin>

          {resp ? (
            <Card
              className="rounded-xl border border-slate-100 bg-white shadow-sm"
              styles={{ header: { borderBottom: "1px solid #f1f5f9" } }}
              title={
                <Space wrap size="small">
                  <Tag color="blue">买入触发 {resp.buy_count} 次</Tag>
                  <Tag color="orange">最大回撤 {(resp.max_drawdown * 100).toFixed(2)}%</Tag>
                  {resp.sell_date ? (
                    <Tag color="green">
                      卖出 {resp.sell_date} @ {resp.sell_price?.toFixed(2)}（{resp.sell_reason}）
                    </Tag>
                  ) : (
                    <Tag>未触发卖出</Tag>
                  )}
                </Space>
              }
            >
              <div ref={chartRef} className="h-[420px] w-full min-w-0" />
            </Card>
          ) : null}

          {rows.length > 0 ? (
            <Card className="rounded-xl border border-slate-100 bg-white shadow-sm" title="每日资产明细" styles={{ header: { borderBottom: "1px solid #f1f5f9" } }}>
              <Table
                rowKey="trade_date"
                size="small"
                columns={columns}
                dataSource={rows}
                pagination={{ pageSize: 20, showSizeChanger: true }}
                scroll={{ x: 760 }}
                rowClassName={(r) => (buyDates.has(r.trade_date) ? "ant-table-row-selected" : "")}
              />
            </Card>
          ) : null}
        </div>
      </div>
    </ConfigProvider>
  );
}
