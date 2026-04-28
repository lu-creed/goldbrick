/**
 * 条件选股页面
 *
 * 页面分为两个 Tab：
 * - 执行选股：选择指标、设定条件、在指定交易日扫描全市场，查看命中的股票列表
 * - 历史记录：每次扫描自动保存，可以回溯历史结果、一键将历史条件套用回表单
 *
 * 与「指标库」的关系：
 * - 指标库负责定义和编辑指标公式（DSL）
 * - 条件选股负责选择指标、设定阈值、在特定交易日执行横向筛选
 */
import {
  Button,
  Card,
  DatePicker,
  Drawer,
  Form,
  InputNumber,
  Popconfirm,
  Radio,
  Segmented,
  Select,
  Skeleton,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { MinusCircleOutlined, PlusOutlined, SwapOutlined } from "@ant-design/icons";
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  deleteScreeningHistory,
  fetchCustomIndicators,
  fetchScreeningHistory,
  fetchScreeningHistoryDetail,
  getApiErrorMessage,
  runScreening,
  fetchWatchlist,
  addToWatchlist,
  removeFromWatchlist,
  type ConditionSpec,
  type StrategyLogic,
  type WatchlistItem,
  type ScreeningHistoryDetail,
  type ScreeningHistoryItem,
  type ScreeningStockRow,
  type UserIndicatorOut,
} from "../api/client";
import { FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";

// ── 工具函数 ──────────────────────────────────────────────────────────────────

/**
 * 从指标定义中提取可参与选股的子线选项
 * 过滤掉 use_in_screening=false 和 auxiliary_only=true 的子线
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

/** 比较运算符的下拉选项 */
const compareOptions = [
  { value: "gt",  label: "大于 >" },
  { value: "gte", label: "大于等于 ≥" },
  { value: "lt",  label: "小于 <" },
  { value: "le",  label: "小于等于 ≤" },
  { value: "eq",  label: "等于 =" },
  { value: "ne",  label: "不等于 ≠" },
];

/** 比较运算符到符号的映射（用于显示） */
const OP_LABEL: Record<string, string> = { gt: ">", gte: "≥", lt: "<", le: "≤", eq: "=", ne: "≠" };

/** 多条件表单里一行条件的字段形状（提交前组装进 StrategyLogic） */
type MultiCondRow = {
  user_indicator_id: number | undefined;
  sub_key: string | undefined;
  compare_op: string;
  threshold: number;
};

/** 把多条件表单值组装成后端要的 StrategyLogic 结构（扁平 AND/OR，每个条件一个组）。 */
function buildLogicFromMultiForm(
  conditions: MultiCondRow[],
  combinerLogic: "AND" | "OR",
  primaryIdx: number,
): StrategyLogic {
  const conds: ConditionSpec[] = conditions.map((c, i) => ({
    id: i + 1,
    user_indicator_id: c.user_indicator_id as number,
    sub_key: c.sub_key ?? null,
    compare_op: c.compare_op,
    threshold: c.threshold,
  }));
  const groups = conds.map((_, i) => ({ id: `G${i + 1}`, condition_ids: [i + 1] }));
  // 单条件时直接指向它；多条件时用 AND/OR 包起来
  const combiner =
    conds.length === 1
      ? { ref: "G1" }
      : {
          op: combinerLogic,
          args: groups.map((g) => ({ ref: g.id })),
        };
  return {
    conditions: conds,
    groups,
    combiner,
    primary_condition_id: primaryIdx + 1,
  };
}

// ── 命中股票表格（选股结果页和历史详情页共用） ─────────────────────────────────

/** 可选的自选股相关 prop：传入后会在表格最右侧显示 ⭐ 收藏按钮 */
interface WatchlistProps {
  watchedSet: Set<string>;                          // 当前已收藏的 ts_code 集合
  onToggle: (ts_code: string, name: string | null) => void;  // 点击星星时的回调
}

function StockTable({ items, watchlistProps }: { items: ScreeningStockRow[]; watchlistProps?: WatchlistProps }) {
  const columns: ColumnsType<ScreeningStockRow> = [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 120,
      render: (v: string) => (
        // 点击代码跳转到该股的 K 线页
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
            {v > 0 ? "+" : ""}{v.toFixed(2)}
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

  // 有 watchlistProps 时在最右侧追加一列 ⭐ 收藏按钮
  if (watchlistProps) {
    columns.push({
      title: "",
      key: "watch",
      width: 36,
      render: (_: unknown, row: ScreeningStockRow) => {
        const watched = watchlistProps.watchedSet.has(row.ts_code);
        return (
          <span
            role="button"
            tabIndex={0}
            style={{ cursor: "pointer", fontSize: 16 }}
            title={watched ? "取消收藏" : "加入自选股"}
            onClick={() => watchlistProps.onToggle(row.ts_code, row.name ?? null)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); watchlistProps.onToggle(row.ts_code, row.name ?? null); } }}
          >
            {watched
              ? <StarFilled style={{ color: "#faad14" }} />
              : <StarOutlined style={{ color: "#8c8c8c" }} />
            }
          </span>
        );
      },
    });
  }

  return (
    <Table
      rowKey="ts_code"
      size="small"
      columns={columns}
      dataSource={items}
      rowClassName={zebraRowClass}
      pagination={{ pageSize: 50, showSizeChanger: true }}
      scroll={{ x: "max-content" }}
    />
  );
}

// ── 主页面组件 ─────────────────────────────────────────────────────────────────

/**
 * 历史详情里渲染多条件快照：每行一个条件 + 组合方式标签。
 */
function LogicDetailView({
  logic,
  indicators,
}: {
  logic: StrategyLogic;
  indicators: UserIndicatorOut[];
}) {
  const combiner = logic.combiner;
  // 推导当前 combiner 的可读描述（扁平 AND/OR 或带括号）
  const combinerText = useMemo(() => {
    function walk(n: typeof combiner): string {
      if (n.ref) return n.ref;
      const op = n.op!;
      const parts = (n.args || []).map(walk);
      if (op === "NOT") return `NOT ${parts[0]}`;
      return parts.join(` ${op} `);
    }
    return walk(combiner);
  }, [combiner]);

  const indMap = useMemo(() => {
    const m = new Map<number, UserIndicatorOut>();
    indicators.forEach((x) => m.set(x.id, x));
    return m;
  }, [indicators]);

  return (
    <Card size="small" title="条件配置" style={{ background: "#fafafa" }}>
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>组合：</Typography.Text>
          <Tag color="blue">{combinerText}</Tag>
        </div>
        {logic.conditions.map((c) => {
          const ind = indMap.get(c.user_indicator_id);
          const isPrimary = c.id === logic.primary_condition_id;
          return (
            <Space key={c.id} size={6} wrap>
              <Tag color={isPrimary ? "gold" : undefined}>G{logic.groups.findIndex((g) => g.condition_ids.includes(c.id)) + 1}</Tag>
              <Typography.Text>
                {ind ? ind.display_name : `指标 ${c.user_indicator_id}`}
                {c.sub_key ? <span style={{ color: "#999", marginLeft: 6 }}>[{c.sub_key}]</span> : null}
              </Typography.Text>
              <Tag>{OP_LABEL[c.compare_op] ?? c.compare_op}</Tag>
              <Typography.Text code>{c.threshold}</Typography.Text>
              {isPrimary ? <Tag color="gold">主排序</Tag> : null}
            </Space>
          );
        })}
      </Space>
    </Card>
  );
}

/**
 * 多条件模式下的单行条件卡片。
 *
 * 一行 = [主排序 Radio] + [指标下拉] + [子线下拉] + [比较] + [阈值] + [删除]
 * 子线下拉的选项随当前行选中的指标动态变化（通过 Form.useWatch 读取同一行的 user_indicator_id）。
 */
function MultiCondRowCard({
  field,
  idx,
  indicators,
  multiForm,
  canRemove,
  onRemove,
}: {
  field: { key: number; name: number };
  idx: number;
  indicators: UserIndicatorOut[];
  multiForm: ReturnType<typeof Form.useForm>[0];
  canRemove: boolean;
  onRemove: () => void;
}) {
  // 监听该行选的指标，动态推导子线选项
  const indicatorId = Form.useWatch(["conditions", field.name, "user_indicator_id"], multiForm) as number | undefined;
  const ind = useMemo(
    () => indicators.find((x) => x.id === indicatorId) ?? null,
    [indicators, indicatorId],
  );
  const subOpts = useMemo(() => {
    if (!ind) return [];
    if (ind.kind === "dsl" && ind.definition) return screeningSubKeys(ind.definition);
    return [{ value: "__expr__", label: "单行表达式" }];
  }, [ind]);
  const isDsl = ind?.kind === "dsl";

  return (
    <Card size="small" style={{ background: "#fafafa" }}>
      <Space wrap align="baseline" size="middle">
        <Radio value={idx} style={{ marginRight: 0 }}>主排序</Radio>

        <Form.Item
          name={[field.name, "user_indicator_id"]}
          label={`条件 ${idx + 1}`}
          rules={[{ required: true, message: "请选择指标" }]}
          style={{ marginBottom: 0 }}
        >
          <Select
            style={{ minWidth: 200 }}
            placeholder="选择指标"
            options={indicators.map((r) => ({ value: r.id, label: `${r.display_name} (${r.code})` }))}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>

        {isDsl ? (
          <Form.Item
            name={[field.name, "sub_key"]}
            label="子线"
            rules={[{ required: true, message: "请选择子线" }]}
            style={{ marginBottom: 0 }}
          >
            <Select style={{ minWidth: 160 }} options={subOpts} placeholder="子线" />
          </Form.Item>
        ) : null}

        <Form.Item
          name={[field.name, "compare_op"]}
          label="比较"
          style={{ marginBottom: 0 }}
        >
          <Select style={{ minWidth: 120 }} options={compareOptions} />
        </Form.Item>

        <Form.Item
          name={[field.name, "threshold"]}
          label="阈值"
          style={{ marginBottom: 0 }}
        >
          <InputNumber step={0.0001} style={{ width: 120 }} />
        </Form.Item>

        {canRemove ? (
          <Button type="text" danger icon={<MinusCircleOutlined />} onClick={onRemove} />
        ) : null}
      </Space>
    </Card>
  );
}

export default function ScreeningPage() {
  // ── 表单 & 选股执行状态 ──────────────────────────────────
  const [form] = Form.useForm();
  const [multiForm] = Form.useForm();
  // 模式切换：单条件（沿用旧 form）/ 多条件（新的多条件 form）
  const [mode, setMode] = useState<"single" | "multi">("single");

  // 从回测页跳转过来时，location.state 中携带回测条件，自动预填选股表单
  const location = useLocation();
  const navigate = useNavigate();
  type FromBacktestState = {
    user_indicator_id: number;
    sub_key: string | null;
    compare_op: string;
    threshold: number;
  };
  const fromBacktestRef = useRef<FromBacktestState | null>(
    (location.state as { from_backtest?: FromBacktestState } | null)?.from_backtest ?? null,
  );
  const [indicators, setIndicators] = useState<UserIndicatorOut[]>([]);
  const [loadingInd, setLoadingInd] = useState(false);
  const [running, setRunning] = useState(false);
  // 当前次选股结果（执行完成后显示）
  const [result, setResult] = useState<{
    trade_date: string;
    scanned: number;
    matched: number;
    note: string | null;
    sub_key: string | null;
    items: ScreeningStockRow[];
    adj_mode?: string;
  } | null>(null);

  // ── 历史记录 tab 状态 ────────────────────────────────────
  const [activeTab, setActiveTab] = useState<"run" | "history">("run");
  const [histories, setHistories] = useState<ScreeningHistoryItem[]>([]);
  const [histLoading, setHistLoading] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRecord, setDetailRecord] = useState<ScreeningHistoryDetail | null>(null);

  // ── 自选股池状态（用于在结果表格中显示 ⭐ 收藏按钮）──────────
  // watchedItems：当前已收藏的完整列表；watchedSet：用 ts_code 做 O(1) 查找
  const [watchedItems, setWatchedItems] = useState<WatchlistItem[]>([]);
  const watchedSet = useMemo(() => new Set(watchedItems.map((w) => w.ts_code)), [watchedItems]);

  // ── 监听当前选中的指标（用于动态显示子线下拉）──────────────
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
    return [{ value: "__expr__", label: "单行表达式" }];
  }, [selectedInd]);

  // ── 数据加载 ─────────────────────────────────────────────

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

  /** 从后端刷新自选股列表（选股完成后调用一次，保证 ⭐ 状态准确） */
  const loadWatched = useCallback(async () => {
    try {
      setWatchedItems(await fetchWatchlist());
    } catch {
      // 自选股加载失败不影响主流程，静默处理
    }
  }, []);

  /**
   * 切换一只股票的收藏状态
   * - 未收藏 → 调用 addToWatchlist，乐观更新列表
   * - 已收藏 → 调用 removeFromWatchlist，乐观更新列表
   */
  const handleWatchToggle = useCallback(async (ts_code: string, name: string | null) => {
    const isWatched = watchedItems.some((w) => w.ts_code === ts_code);
    if (isWatched) {
      setWatchedItems((prev) => prev.filter((w) => w.ts_code !== ts_code));
      try {
        await removeFromWatchlist(ts_code);
        message.success(`已取消收藏 ${ts_code}`);
      } catch (e) {
        message.error(getApiErrorMessage(e));
        void loadWatched(); // 失败后重新同步真实状态
      }
    } else {
      const newItem: WatchlistItem = { ts_code, name, note: null, created_at: new Date().toISOString() };
      setWatchedItems((prev) => [newItem, ...prev]);
      try {
        await addToWatchlist(ts_code, name, null);
        message.success(`已加入自选股 ${ts_code}`);
      } catch (e) {
        message.error(getApiErrorMessage(e));
        void loadWatched();
      }
    }
  }, [watchedItems, loadWatched]);

  const loadHistory = useCallback(async () => {
    setHistLoading(true);
    try {
      // 一次加载最多 100 条，本地分页，避免多余的接口调用
      const rows = await fetchScreeningHistory({ page_size: 100 });
      setHistories(rows);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setHistLoading(false);
    }
  }, []);

  // 首次挂载：同时加载指标列表和自选股（两者独立，并发执行）
  useEffect(() => { void loadIndicators(); void loadWatched(); }, [loadIndicators, loadWatched]);

  // 指标列表加载完成后，若携带了来自回测页的参数则自动填入
  useEffect(() => {
    if (!indicators.length || !fromBacktestRef.current) return;
    const { user_indicator_id, sub_key, compare_op, threshold } = fromBacktestRef.current;
    fromBacktestRef.current = null; // 只消费一次
    form.setFieldsValue({
      user_indicator_id,
      sub_key: sub_key ?? undefined,
      compare_op,
      threshold,
    });
    message.info("已从回测页导入指标条件");
  }, [indicators, form]);

  // 切换到历史 tab 时自动加载数据
  useEffect(() => {
    if (activeTab === "history") void loadHistory();
  }, [activeTab, loadHistory]);

  // 选中指标变化时，自动填入第一个子线选项
  useEffect(() => {
    if (!selectedInd) { form.setFieldValue("sub_key", undefined); return; }
    const first = subOpts[0]?.value;
    if (first != null && form.getFieldValue("sub_key") == null) {
      form.setFieldValue("sub_key", selectedInd.kind === "legacy" ? "__expr__" : first);
    }
  }, [selectedInd, subOpts, form]);

  // ── 历史记录操作 ─────────────────────────────────────────

  /** 打开某条历史记录的详情 Drawer */
  const openDetail = useCallback(async (id: number) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailRecord(null);
    try {
      const d = await fetchScreeningHistoryDetail(id);
      setDetailRecord(d);
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  /** 删除一条历史记录并刷新列表 */
  const handleDelete = useCallback(async (id: number) => {
    try {
      await deleteScreeningHistory(id);
      message.success("已删除");
      void loadHistory();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    }
  }, [loadHistory]);

  /**
   * 将历史记录的选股条件套用回表单
   * 方便用户在历史结果基础上调整参数后重新执行
   */
  const restoreConditions = useCallback((rec: ScreeningHistoryDetail) => {
    const matchedInd = rec.user_indicator_id != null
      ? indicators.find((x) => x.id === rec.user_indicator_id)
      : null;
    form.setFieldsValue({
      trade_date: dayjs(rec.trade_date),
      user_indicator_id: matchedInd?.id ?? undefined,
      sub_key: rec.sub_key ?? undefined,
      compare_op: rec.compare_op ?? "gt",
      threshold: rec.threshold,
    });
    setDetailOpen(false);
    setActiveTab("run");
    message.success("已套用历史条件，可修改后重新执行");
  }, [form, indicators]);

  // ── 执行选股 ─────────────────────────────────────────────

  const onRun = async () => {
    // 多条件模式：组装 logic 提交
    if (mode === "multi") {
      try {
        const v = await multiForm.validateFields();
        const rows = (v.conditions as MultiCondRow[]) || [];
        if (rows.length === 0) {
          message.warning("请至少添加一个条件");
          return;
        }
        const logic = buildLogicFromMultiForm(
          rows,
          (v.combiner_logic as "AND" | "OR") || "AND",
          Number(v.primary_cond_idx ?? 0),
        );
        const td = (v.trade_date as Dayjs).format("YYYY-MM-DD");
        setRunning(true);
        setResult(null);
        try {
          const out = await runScreening({
            trade_date: td,
            logic,
            max_scan: (v.max_scan as number) ?? 6000,
          });
          setResult({
            trade_date: out.trade_date,
            scanned: out.scanned,
            matched: out.matched,
            note: out.note,
            sub_key: out.sub_key,
            items: out.items,
            adj_mode: out.adj_mode,
          });
          message.success(`完成：扫描 ${out.scanned}，命中 ${out.matched}`);
        } catch (e) {
          message.error(getApiErrorMessage(e));
        } finally {
          setRunning(false);
        }
      } catch {
        // 表单校验失败，Ant Design 会自动高亮出错字段
      }
      return;
    }

    // 单条件模式：维持原有行为
    try {
      const v = await form.validateFields();
      const td = (v.trade_date as Dayjs).format("YYYY-MM-DD");
      setRunning(true);
      setResult(null);
      try {
        const out = await runScreening({
          trade_date: td,
          user_indicator_id: v.user_indicator_id as number,
          // 旧版表达式指标不传 sub_key，后端直接用 expr
          sub_key: selectedInd?.kind === "legacy" ? undefined : (v.sub_key as string | undefined),
          compare_op: v.compare_op as string,
          threshold: v.threshold as number,
          max_scan: (v.max_scan as number) ?? 6000,
        });
        setResult({
          trade_date: out.trade_date,
          scanned: out.scanned,
          matched: out.matched,
          note: out.note,
          sub_key: out.sub_key,
          items: out.items,
          adj_mode: out.adj_mode,
        });
        message.success(`完成：扫描 ${out.scanned}，命中 ${out.matched}`);
      } catch (e) {
        message.error(getApiErrorMessage(e));
      } finally {
        setRunning(false);
      }
    } catch {
      // 表单校验失败，Ant Design 会自动高亮出错字段，无需额外处理
    }
  };

  // ── 历史记录表格列定义 ────────────────────────────────────

  const histColumns: ColumnsType<ScreeningHistoryItem> = [
    {
      title: "执行时间",
      dataIndex: "created_at",
      width: 140,
      render: (v: string) => v ? dayjs(v).format("MM-DD HH:mm") : "—",
    },
    {
      title: "交易日",
      dataIndex: "trade_date",
      width: 100,
    },
    {
      title: "指标名称",
      key: "indicator",
      ellipsis: true,
      render: (_: unknown, r: ScreeningHistoryItem) => (
        <Space direction="vertical" size={0}>
          <span>{r.indicator_name || r.indicator_code}</span>
          {r.sub_key ? <Tag style={{ fontSize: 11 }}>{r.sub_key}</Tag> : null}
        </Space>
      ),
    },
    {
      title: "筛选条件",
      key: "condition",
      width: 140,
      render: (_: unknown, r: ScreeningHistoryItem) => {
        if (r.is_multi) {
          return <Tag color="purple">多条件</Tag>;
        }
        return r.compare_op
          ? `${OP_LABEL[r.compare_op] ?? r.compare_op} ${r.threshold}`
          : "—";
      },
    },
    {
      title: "命中 / 扫描",
      key: "matched",
      width: 110,
      align: "center" as const,
      render: (_: unknown, r: ScreeningHistoryItem) => (
        <Space size={4}>
          <Tag color="blue">{r.matched}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>/ {r.scanned}</Typography.Text>
        </Space>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 130,
      render: (_: unknown, r: ScreeningHistoryItem) => (
        <Space>
          <Button size="small" type="link" onClick={() => void openDetail(r.id)}>
            查看详情
          </Button>
          <Popconfirm
            title="确认删除这条历史记录？"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => void handleDelete(r.id)}
          >
            <Button size="small" type="link" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── 渲染 ─────────────────────────────────────────────────

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1200 }}>
      <div>
        <Typography.Title level={4} style={{ margin: 0 }}>条件选股</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
          基于自定义指标，在选定交易日对全市场个股做横向筛选。每次扫描结果自动保存，可在「历史记录」中回溯。
        </Typography.Paragraph>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as "run" | "history")}
        items={[
          {
            key: "run",
            label: "执行选股",
            children: (
              <Space direction="vertical" size="large" style={{ width: "100%" }}>
                {/* 选股条件表单 */}
                <Card>
                  {loadingInd ? (
                    <Skeleton active paragraph={{ rows: 2 }} />
                  ) : (
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      {/* 单条件 / 多条件 切换 */}
                      <Segmented
                        value={mode}
                        onChange={(v) => setMode(v as "single" | "multi")}
                        options={[
                          { label: "单条件", value: "single" },
                          { label: "多条件 (AND / OR)", value: "multi" },
                        ]}
                      />

                      {mode === "single" ? (
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
                      ) : (
                        <Form
                          form={multiForm}
                          layout="vertical"
                          initialValues={{
                            trade_date: dayjs(),
                            max_scan: 6000,
                            combiner_logic: "AND",
                            primary_cond_idx: 0,
                            conditions: [
                              { compare_op: "gt", threshold: 0 },
                            ],
                          }}
                        >
                          <Space wrap size="middle">
                            <Form.Item name="trade_date" label="交易日" rules={[{ required: true }]} style={{ marginBottom: 0 }}>
                              <DatePicker />
                            </Form.Item>
                            <Form.Item name="combiner_logic" label="组合方式" style={{ marginBottom: 0 }}>
                              <Segmented options={[{ label: "全部满足 (AND)", value: "AND" }, { label: "任一满足 (OR)", value: "OR" }]} />
                            </Form.Item>
                            <Form.Item name="max_scan" label="最多扫描" style={{ marginBottom: 0 }}>
                              <InputNumber min={500} max={8000} step={500} style={{ width: 120 }} />
                            </Form.Item>
                          </Space>

                          {/* 条件列表（动态增删） */}
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            💡 勾选"主排序"的条件会用其指标值给结果降序排列；命中的股票在结果中按主条件值从大到小展示。
                          </Typography.Text>
                          <Form.Item name="primary_cond_idx" noStyle>
                            <Radio.Group style={{ width: "100%" }}>
                              <Form.List name="conditions">
                                {(fields, { add, remove }) => (
                                  <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 12 }}>
                                    {fields.map((field, idx) => (
                                      <MultiCondRowCard
                                        key={field.key}
                                        field={field}
                                        idx={idx}
                                        indicators={indicators}
                                        multiForm={multiForm}
                                        canRemove={fields.length > 1}
                                        onRemove={() => remove(field.name)}
                                      />
                                    ))}
                                    <Button
                                      type="dashed"
                                      block
                                      icon={<PlusOutlined />}
                                      onClick={() => add({ compare_op: "gt", threshold: 0 })}
                                    >
                                      添加条件
                                    </Button>
                                  </Space>
                                )}
                              </Form.List>
                            </Radio.Group>
                          </Form.Item>

                          <Form.Item style={{ marginTop: 12, marginBottom: 0 }}>
                            <Button type="primary" onClick={() => void onRun()} loading={running}>
                              执行选股
                            </Button>
                          </Form.Item>
                        </Form>
                      )}
                    </Space>
                  )}
                </Card>

                {/* 选股结果（执行完成后显示） */}
                {result ? (
                  <Card
                    title={
                      <Space wrap>
                        <span>结果</span>
                        <Tag>
                          {result.trade_date} · 扫描 {result.scanned} · 命中 {result.matched}
                        </Tag>
                        {result.sub_key ? <Tag color="blue">子线 {result.sub_key}</Tag> : null}
                        <Tag color="geekblue">
                          {result.adj_mode === "qfq" ? "前复权口径" : (result.adj_mode || "未复权口径")}
                        </Tag>
                      </Space>
                    }
                  >
                    {result.note ? <Typography.Text type="warning">{result.note}</Typography.Text> : null}
                    <StockTable
                      items={result.items}
                      watchlistProps={{ watchedSet, onToggle: handleWatchToggle }}
                    />
                    {/* 快捷入口：将当前选股条件同步到回测页 */}
                    <div style={{ marginTop: 12, textAlign: "right" }}>
                      <Button
                        icon={<SwapOutlined />}
                        onClick={() => {
                          const uid = form.getFieldValue("user_indicator_id") as number | undefined;
                          if (!uid) return;
                          navigate("/backtest", {
                            state: {
                              from_screening: {
                                user_indicator_id: uid,
                                sub_key: result.sub_key,
                                buy_op: form.getFieldValue("compare_op") as string,
                                buy_threshold: form.getFieldValue("threshold") as number,
                              },
                            },
                          });
                        }}
                      >
                        将此条件转为回测
                      </Button>
                    </div>
                  </Card>
                ) : null}
              </Space>
            ),
          },
          {
            key: "history",
            label: "历史记录",
            children: (
              <Card>
                <Table<ScreeningHistoryItem>
                  rowKey="id"
                  size="small"
                  columns={histColumns}
                  dataSource={histories}
                  loading={histLoading}
                  rowClassName={zebraRowClass}
                  pagination={{
                    pageSize: 20,
                    showSizeChanger: false,
                    showTotal: (t) => `共 ${t} 条记录`,
                  }}
                  scroll={{ x: "max-content" }}
                  locale={{ emptyText: "暂无历史记录，执行一次选股后自动保存" }}
                />
              </Card>
            ),
          },
        ]}
      />

      {/* 历史详情 Drawer：展示命中股票列表，支持一键套用条件 */}
      <Drawer
        title={
          detailRecord
            ? `${detailRecord.trade_date} · ${detailRecord.indicator_name || detailRecord.indicator_code}`
            : "选股历史详情"
        }
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={Math.min(860, window.innerWidth * 0.95)}
        extra={
          // "套用此条件"按钮：将历史参数填回表单，方便用户在此基础上微调重跑
          detailRecord ? (
            <Button type="primary" size="small" onClick={() => restoreConditions(detailRecord)}>
              套用此条件
            </Button>
          ) : null
        }
      >
        {detailLoading ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : detailRecord ? (
          <Space direction="vertical" style={{ width: "100%" }} size="large">
            {/* 选股参数摘要标签 */}
            <Space wrap size={8}>
              <Tag>交易日 {detailRecord.trade_date}</Tag>
              <Tag color="blue">命中 {detailRecord.matched} 只</Tag>
              <Tag>扫描 {detailRecord.scanned} 只</Tag>
              {detailRecord.is_multi ? (
                <Tag color="purple">多条件</Tag>
              ) : detailRecord.compare_op ? (
                <Tag color="geekblue">
                  {detailRecord.sub_key}{" "}
                  {OP_LABEL[detailRecord.compare_op] ?? detailRecord.compare_op}{" "}
                  {detailRecord.threshold}
                </Tag>
              ) : null}
            </Space>
            {/* 多条件快照详情 */}
            {detailRecord.is_multi && detailRecord.logic ? (
              <LogicDetailView logic={detailRecord.logic} indicators={indicators} />
            ) : null}
            {/* 命中股票列表 */}
            <StockTable items={detailRecord.items} />
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
