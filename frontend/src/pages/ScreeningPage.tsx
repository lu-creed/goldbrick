/**
 * 条件选股：与指标库同一套 DSL / expr 求值，在指定交易日做横向筛选。
 */
import { Button, Card, DatePicker, Form, InputNumber, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  fetchCustomIndicators,
  getApiErrorMessage,
  runScreening,
  type ScreeningStockRow,
  type UserIndicatorOut,
} from "../api/client";

function screeningSubKeys(def: Record<string, unknown> | null | undefined): { value: string; label: string }[] {
  const subs = def?.sub_indicators as
    | { key?: string; name?: string; use_in_screening?: boolean; auxiliary_only?: boolean }[]
    | undefined;
  if (!Array.isArray(subs)) return [];
  return subs
    .filter((s) => s.key && s.use_in_screening !== false && !s.auxiliary_only)
    .map((s) => ({ value: s.key!, label: `${s.name || s.key} (${s.key})` }));
}

const compareOptions = [
  { value: "gt", label: "大于 >" },
  { value: "gte", label: "大于等于 ≥" },
  { value: "lt", label: "小于 <" },
  { value: "le", label: "小于等于 ≤" },
  { value: "eq", label: "等于 =" },
  { value: "ne", label: "不等于 ≠" },
];

export default function ScreeningPage() {
  const [form] = Form.useForm();
  const [indicators, setIndicators] = useState<UserIndicatorOut[]>([]);
  const [loadingInd, setLoadingInd] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{
    trade_date: string;
    scanned: number;
    matched: number;
    note: string | null;
    sub_key: string | null;
    items: ScreeningStockRow[];
  } | null>(null);

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
    return [{ value: "__expr__", label: "旧版表达式（单值）" }];
  }, [selectedInd]);

  const loadIndicators = useCallback(async () => {
    setLoadingInd(true);
    try {
      const rows = await fetchCustomIndicators();
      setIndicators(rows);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoadingInd(false);
    }
  }, []);

  useEffect(() => {
    void loadIndicators();
  }, [loadIndicators]);

  useEffect(() => {
    if (!selectedInd) {
      form.setFieldValue("sub_key", undefined);
      return;
    }
    const o = subOpts[0]?.value;
    if (o != null && form.getFieldValue("sub_key") == null) {
      form.setFieldValue("sub_key", selectedInd.kind === "legacy" ? "__expr__" : o);
    }
  }, [selectedInd, subOpts, form]);

  const columns: ColumnsType<ScreeningStockRow> = [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 120,
      render: (v: string) => (
        <Link to={`/?ts_code=${encodeURIComponent(v)}`}>{v}</Link>
      ),
    },
    { title: "名称", dataIndex: "name", ellipsis: true },
    { title: "收盘", dataIndex: "close", width: 100, render: (v: number) => v.toFixed(3) },
    {
      title: "涨跌幅%",
      dataIndex: "pct_change",
      width: 100,
      render: (v: number | null) => (v == null ? "—" : v.toFixed(2)),
    },
    {
      title: "指标值",
      dataIndex: "indicator_value",
      width: 120,
      render: (v: number) => v.toFixed(6),
    },
  ];

  const onRun = async () => {
    try {
      const v = await form.validateFields();
      const td = (v.trade_date as Dayjs).format("YYYY-MM-DD");
      setRunning(true);
      setResult(null);
      try {
        const out = await runScreening({
          trade_date: td,
          user_indicator_id: v.user_indicator_id,
          sub_key: selectedInd?.kind === "legacy" ? undefined : v.sub_key,
          compare_op: v.compare_op,
          threshold: v.threshold,
          max_scan: v.max_scan ?? 6000,
        });
        setResult({
          trade_date: out.trade_date,
          scanned: out.scanned,
          matched: out.matched,
          note: out.note,
          sub_key: out.sub_key,
          items: out.items,
        });
        message.success(`完成：扫描 ${out.scanned}，命中 ${out.matched}`);
      } catch (e) {
        message.error(getApiErrorMessage(e));
      } finally {
        setRunning(false);
      }
    } catch {
      /* validate */
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1200 }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        条件选股
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        与指标库<strong>同一套</strong>求值逻辑；选股使用<strong>未复权日线</strong>截面（与指标试算一致）。数据量大时扫描可能需要数十秒。
      </Typography.Paragraph>
      <Card loading={loadingInd}>
        <Form
          form={form}
          layout="inline"
          initialValues={{
            trade_date: dayjs(),
            compare_op: "gt",
            threshold: 0,
            max_scan: 6000,
          }}
          style={{ rowGap: 16 }}
        >
          <Form.Item name="trade_date" label="交易日" rules={[{ required: true }]}>
            <DatePicker />
          </Form.Item>
          <Form.Item name="user_indicator_id" label="自定义指标" rules={[{ required: true, message: "请选择" }]}>
            <Select
              style={{ minWidth: 220 }}
              placeholder="选择已保存指标"
              options={indicators.map((r) => ({
                value: r.id,
                label: `${r.display_name} (${r.code})`,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
          {selectedInd?.kind === "dsl" ? (
            <Form.Item name="sub_key" label="子线" rules={[{ required: true }]}>
              <Select style={{ minWidth: 200 }} options={subOpts} placeholder="参与选股的子线" />
            </Form.Item>
          ) : null}
          <Form.Item name="compare_op" label="比较">
            <Select style={{ minWidth: 140 }} options={compareOptions} />
          </Form.Item>
          <Form.Item name="threshold" label="阈值">
            <InputNumber step={0.0001} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="max_scan" label="最多扫描只数" tooltip="全市场个股上限，防超时">
            <InputNumber min={500} max={8000} step={500} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={() => void onRun()} loading={running}>
              执行选股
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {result ? (
        <Card
          title={
            <Space>
              <span>结果</span>
              <Tag>
                {result.trade_date} · 扫描 {result.scanned} · 命中 {result.matched}
              </Tag>
              {result.sub_key ? <Tag color="blue">子线 {result.sub_key}</Tag> : null}
            </Space>
          }
        >
          {result.note ? <Typography.Text type="warning">{result.note}</Typography.Text> : null}
          <Table
            rowKey="ts_code"
            size="small"
            columns={columns}
            dataSource={result.items}
            pagination={{ pageSize: 50, showSizeChanger: true }}
            scroll={{ x: 640 }}
          />
        </Card>
      ) : null}
    </Space>
  );
}
