/**
 * 条件选股页面
 *
 * 功能：用用户自定义的 DSL 指标，在某一交易日对全市场个股做横向筛选。
 * 例如：找出所有"MA5 > MA20"（均线金叉）的股票。
 *
 * 与「指标库」的关系：
 * - 指标库负责定义和编辑指标公式
 * - 条件选股负责选择指标、设定阈值、在特定日期执行扫描
 * - 两者使用相同的后端求值引擎（DSL），结果一致
 *
 * 使用场景：
 * 1. 选择一个已保存的自定义指标
 * 2. 选择参与选股的子线（指标可能有多条输出线）
 * 3. 设定比较条件（如"大于 0"）
 * 4. 点击执行，查看满足条件的股票列表
 */
import {
  Button,
  Card,
  DatePicker,
  Form,
  InputNumber,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
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
import { FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";

/**
 * 从自定义指标定义中提取「可参与选股」的子线选项
 * 过滤掉 use_in_screening=false 和 auxiliary_only=true 的子线
 *
 * @param def - 指标定义对象（来自后端的 JSON 配置）
 * @returns 子线下拉选项列表
 */
function screeningSubKeys(def: Record<string, unknown> | null | undefined): { value: string; label: string }[] {
  const subs = def?.sub_indicators as
    | { key?: string; name?: string; use_in_screening?: boolean; auxiliary_only?: boolean }[]
    | undefined;
  if (!Array.isArray(subs)) return [];
  return subs
    .filter((s) => s.key && s.use_in_screening !== false && !s.auxiliary_only)
    .map((s) => ({ value: s.key!, label: `${s.name || s.key} (${s.key})` }));
}

/** 比较运算符选项（用于设定筛选条件，如「大于 > 0」） */
const compareOptions = [
  { value: "gt", label: "大于 >" },
  { value: "gte", label: "大于等于 ≥" },
  { value: "lt", label: "小于 <" },
  { value: "le", label: "小于等于 ≤" },
  { value: "eq", label: "等于 =" },
  { value: "ne", label: "不等于 ≠" },
];

export default function ScreeningPage() {
  // form：选股条件表单实例
  const [form] = Form.useForm();
  // indicators：已保存的自定义指标列表（下拉选项来源）
  const [indicators, setIndicators] = useState<UserIndicatorOut[]>([]);
  // loadingInd：是否正在加载指标列表
  const [loadingInd, setLoadingInd] = useState(false);
  // running：是否正在执行选股（防止重复提交）
  const [running, setRunning] = useState(false);
  // result：选股结果（null 表示尚未执行或执行中）
  const [result, setResult] = useState<{
    trade_date: string;
    scanned: number;
    matched: number;
    note: string | null;
    sub_key: string | null;
    items: ScreeningStockRow[];
  } | null>(null);

  // 实时监听表单中选中的指标 ID（用于动态显示对应的子线选项）
  const selectedId = Form.useWatch("user_indicator_id", form);
  // 根据 ID 找到对应的指标对象
  const selectedInd = useMemo(
    () => indicators.find((x) => x.id === selectedId) ?? null,
    [indicators, selectedId],
  );
  // 当前选中指标的可选子线列表（DSL 指标有多子线，旧版表达式只有单值）
  const subOpts = useMemo(() => {
    if (!selectedInd) return [];
    if (selectedInd.kind === "dsl" && selectedInd.definition) {
      return screeningSubKeys(selectedInd.definition);
    }
    return [{ value: "__expr__", label: "单行表达式" }];
  }, [selectedInd]);

  /** 加载已保存的自定义指标列表 */
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

  // 组件挂载时加载指标列表
  useEffect(() => {
    void loadIndicators();
  }, [loadIndicators]);

  // 当选中的指标变化时，自动填入第一个子线选项
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

  /** 结果表格列定义：代码、名称、收盘价、涨跌幅、指标值 */
  const columns: ColumnsType<ScreeningStockRow> = [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 120,
      render: (v: string) => (
        // 点击代码可跳转到该股的 K 线页
        <Link to={`/?ts_code=${encodeURIComponent(v)}`}>{v}</Link>
      ),
    },
    { title: "名称", dataIndex: "name", ellipsis: true },
    {
      title: "收盘",
      dataIndex: "close",
      width: 100,
      align: "right" as const,
      render: (v: number) => v.toFixed(3),
    },
    {
      title: "涨跌幅%",
      dataIndex: "pct_change",
      width: 100,
      align: "right" as const,
      render: (v: number | null) =>
        v == null ? "—" : (
          <span style={{ color: v > 0 ? RISE_COLOR : v < 0 ? FALL_COLOR : FLAT_COLOR }}>
            {v > 0 ? "+" : ""}
            {v.toFixed(2)}
          </span>
        ),
    },
    {
      title: "指标值",
      dataIndex: "indicator_value",
      width: 120,
      align: "right" as const,
      render: (v: number) => v.toFixed(6),
    },
  ];

  /**
   * 执行选股
   * 先校验表单，再调用后端 API 开始扫描
   * max_scan：最多扫描的股票数（防止超时）
   */
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
          // 旧版表达式指标不传 sub_key（后端会直接用 expr）
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
      /* 表单校验失败，不需要处理 */
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1200 }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        条件选股
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
        基于自定义指标，在选定交易日对全市场个股做横向筛选。数据量大时扫描可能需要数十秒。
      </Typography.Paragraph>

      {/* ── 选股条件表单 ──────────────────────────────────── */}
      <Card>
        {/* 加载指标列表时显示骨架屏 */}
        {loadingInd ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
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
            {/* 交易日：选择要扫描哪一天的截面数据 */}
            <Form.Item name="trade_date" label="交易日" rules={[{ required: true }]}>
              <DatePicker />
            </Form.Item>

            {/* 自定义指标：选择用哪个指标的值来筛选 */}
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

            {/* 子线：DSL 指标可能有多条输出线，这里选择用哪条 */}
            {selectedInd?.kind === "dsl" ? (
              <Form.Item name="sub_key" label="子线" rules={[{ required: true }]}>
                <Select style={{ minWidth: 200 }} options={subOpts} placeholder="参与选股的子线" />
              </Form.Item>
            ) : null}

            {/* 比较方式：指标值与阈值的比较运算符 */}
            <Form.Item name="compare_op" label="比较">
              <Select style={{ minWidth: 140 }} options={compareOptions} />
            </Form.Item>

            {/* 阈值：指标值与该值进行比较 */}
            <Form.Item name="threshold" label="阈值">
              <InputNumber step={0.0001} style={{ width: 120 }} />
            </Form.Item>

            {/* 最多扫描：防止全市场扫描超时，设置上限 */}
            <Form.Item name="max_scan" label="最多扫描只数" tooltip="全市场个股上限，防超时">
              <InputNumber min={500} max={8000} step={500} style={{ width: 120 }} />
            </Form.Item>

            {/* 执行选股：loading={running} 防止重复点击 */}
            <Form.Item>
              <Button type="primary" onClick={() => void onRun()} loading={running}>
                执行选股
              </Button>
            </Form.Item>
          </Form>
        )}
      </Card>

      {/* ── 选股结果表格（执行完成后显示）──────────────────── */}
      {result ? (
        <Card
          title={
            <Space>
              <span>结果</span>
              {/* 统计信息：交易日 + 扫描总数 + 命中数 */}
              <Tag>
                {result.trade_date} · 扫描 {result.scanned} · 命中 {result.matched}
              </Tag>
              {result.sub_key ? <Tag color="blue">子线 {result.sub_key}</Tag> : null}
            </Space>
          }
        >
          {/* 后端可能返回警告提示（如数据不完整） */}
          {result.note ? <Typography.Text type="warning">{result.note}</Typography.Text> : null}
          <Table
            rowKey="ts_code"
            size="small"
            columns={columns}
            dataSource={result.items}
            rowClassName={zebraRowClass} // 斑马纹：偶数行略微加亮
            pagination={{ pageSize: 50, showSizeChanger: true }}
            scroll={{ x: 640 }}
          />
        </Card>
      ) : null}
    </Space>
  );
}
