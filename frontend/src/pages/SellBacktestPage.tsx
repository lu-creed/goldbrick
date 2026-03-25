import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  InputNumber,
  Radio,
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
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchSymbols, getApiErrorMessage, runBuySellBacktest, type BacktestDailyPoint } from "../api/client";

type FormVals = {
  ts_code: string;
  range: [Dayjs, Dayjs];
  buy_date: Dayjs;
  buy_price: number;
  buy_qty: number;
  initial_cash: number;
  sell_target_price?: number;
  sell_target_return?: number;
  sell_target_date?: Dayjs;
  sell_logic: "or" | "and";
};

export default function SellBacktestPage() {
  const [form] = Form.useForm<FormVals>();
  const [symbols, setSymbols] = useState<{ label: string; value: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [rows, setRows] = useState<BacktestDailyPoint[]>([]);
  const [maxDrawdown, setMaxDrawdown] = useState<number>(0);
  const [buyDateMark, setBuyDateMark] = useState<string | null>(null);
  const [sellDateMark, setSellDateMark] = useState<string | null>(null);
  const [sellInfo, setSellInfo] = useState<string>("-");
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartIns = useRef<echarts.ECharts | null>(null);

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

  const onRun = async () => {
    const v = await form.validateFields();
    setRunning(true);
    setError(null);
    try {
      const resp = await runBuySellBacktest({
        ts_code: v.ts_code,
        start_date: v.range[0].format("YYYY-MM-DD"),
        end_date: v.range[1].format("YYYY-MM-DD"),
        buy_date: v.buy_date.format("YYYY-MM-DD"),
        buy_price: v.buy_price,
        buy_qty: v.buy_qty,
        initial_cash: v.initial_cash,
        sell_target_price: v.sell_target_price ?? undefined,
        sell_target_return: v.sell_target_return ?? undefined,
        sell_target_date: v.sell_target_date ? v.sell_target_date.format("YYYY-MM-DD") : undefined,
        sell_logic: v.sell_logic,
      });
      setRows(resp.daily);
      setMaxDrawdown(resp.max_drawdown);
      setBuyDateMark(resp.buy_date);
      setSellDateMark(resp.sell_date);
      setSellInfo(resp.sell_date ? `${resp.sell_date} @ ${resp.sell_price?.toFixed(2) ?? "-"} (${resp.sell_reason ?? "-"})` : "未触发卖出");
      message.success("回测完成");
    } catch (e) {
      setRows([]);
      setBuyDateMark(null);
      setSellDateMark(null);
      setSellInfo("-");
      setError(getApiErrorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  const option = useMemo(() => {
    const markData: Array<{ name: string; xAxis: string; yAxis: number | undefined }> = [];
    if (buyDateMark) markData.push({ name: "买入点", xAxis: buyDateMark, yAxis: rows.find((r) => r.trade_date === buyDateMark)?.total_asset });
    if (sellDateMark) markData.push({ name: "卖出点", xAxis: sellDateMark, yAxis: rows.find((r) => r.trade_date === sellDateMark)?.total_asset });
    return {
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { data: ["总资产", "股票市值", "现金"] },
      xAxis: { type: "category", data: rows.map((r) => r.trade_date) },
      yAxis: { type: "value", scale: true },
      series: [
        {
          name: "总资产",
          type: "line",
          symbol: "none",
          data: rows.map((r) => Number(r.total_asset.toFixed(2))),
          markPoint: markData.length ? { data: markData } : undefined,
        },
        { name: "股票市值", type: "line", symbol: "none", data: rows.map((r) => Number(r.stock_value.toFixed(2))) },
        { name: "现金", type: "line", symbol: "none", data: rows.map((r) => Number(r.cash_value.toFixed(2))) },
      ],
    };
  }, [rows, buyDateMark, sellDateMark]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    if (!chartIns.current) chartIns.current = echarts.init(el);
    chartIns.current.setOption(option, true);
    const ro = new ResizeObserver(() => chartIns.current?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, [option]);

  const columns: ColumnsType<BacktestDailyPoint> = [
    { title: "日期", dataIndex: "trade_date", width: 110 },
    { title: "收盘价", dataIndex: "close", width: 100, render: (v: number) => v.toFixed(2) },
    { title: "股票市值", dataIndex: "stock_value", width: 120, render: (v: number) => v.toFixed(2) },
    { title: "现金", dataIndex: "cash_value", width: 120, render: (v: number) => v.toFixed(2) },
    { title: "总资产", dataIndex: "total_asset", width: 120, render: (v: number) => v.toFixed(2) },
    { title: "当日盈亏", dataIndex: "daily_pnl", width: 120, render: (v: number) => v.toFixed(2) },
    { title: "累计收益率", dataIndex: "cum_return", width: 120, render: (v: number) => `${(v * 100).toFixed(2)}%` },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        卖出回测
      </Typography.Title>
      {error ? <Alert type="error" showIcon message="回测失败" description={error} /> : null}
      <Card loading={loading}>
        <Form form={form} layout="inline" initialValues={{ buy_qty: 100, initial_cash: 100000, sell_logic: "or" }} style={{ rowGap: 12, columnGap: 8 }}>
          <Form.Item name="ts_code" rules={[{ required: true, message: "请选择标的" }]}>
            <Select style={{ width: 220 }} showSearch options={symbols} placeholder="标的" optionFilterProp="label" />
          </Form.Item>
          <Form.Item name="range" rules={[{ required: true, message: "选择回测区间" }]}>
            <DatePicker.RangePicker />
          </Form.Item>
          <Form.Item name="buy_date" rules={[{ required: true, message: "选择买入日" }]}>
            <DatePicker placeholder="买入日" />
          </Form.Item>
          <Form.Item name="buy_price" rules={[{ required: true, message: "输入买入价" }]}>
            <InputNumber min={0.01} precision={3} placeholder="买入价" />
          </Form.Item>
          <Form.Item name="buy_qty" rules={[{ required: true, message: "输入买入数量" }]}>
            <InputNumber min={1} precision={0} placeholder="数量" />
          </Form.Item>
          <Form.Item name="initial_cash" rules={[{ required: true, message: "输入初始现金" }]}>
            <InputNumber min={0} precision={2} placeholder="初始现金" />
          </Form.Item>
          <Form.Item label="目标价" name="sell_target_price">
            <InputNumber min={0.01} precision={3} placeholder="可空" />
          </Form.Item>
          <Form.Item label="目标收益率" name="sell_target_return" tooltip="输入小数，例如 0.1 表示 10%">
            <InputNumber min={-1} max={10} step={0.01} precision={4} placeholder="可空" />
          </Form.Item>
          <Form.Item label="目标日期" name="sell_target_date">
            <DatePicker placeholder="可空" />
          </Form.Item>
          <Form.Item label="条件关系" name="sell_logic">
            <Radio.Group options={[{ label: "满足任一(OR)", value: "or" }, { label: "全部满足(AND)", value: "and" }]} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={running} onClick={() => void onRun()}>
              执行回测
            </Button>
          </Form.Item>
        </Form>
      </Card>
      <Card title={<Space><span>{`资产曲线（最大回撤 ${(maxDrawdown * 100).toFixed(2)}%）`}</span><Tag color="blue">{`卖出结果: ${sellInfo}`}</Tag></Space>}>
        <div style={{ position: "relative", minHeight: 360 }}>
          {running ? (
            <Spin style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2 }} />
          ) : null}
          <div ref={chartRef} style={{ width: "100%", height: 360 }} />
        </div>
      </Card>
      <Card title="每日资产明细">
        <Table
          rowKey="trade_date"
          size="small"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 12 }}
          scroll={{ x: 900 }}
          onRow={(record) => ({
            style:
              record.trade_date === buyDateMark
                ? { background: "#fff7e6" }
                : record.trade_date === sellDateMark
                  ? { background: "#f6ffed" }
                  : undefined,
          })}
        />
      </Card>
    </Space>
  );
}
