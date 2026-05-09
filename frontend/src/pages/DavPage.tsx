/**
 * 大V看板（Mr. Dang ABCD 分类框架）
 *
 * 按 A/B/C/D 四类展示用户标注的股票，手动维护派息率与 EPS，
 * 自动从本地日线取最新价并计算预期股息率（派息率% × EPS ÷ 当前价 × 100）。
 */
import {
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  MinusCircleOutlined,
  PlusOutlined,
  RobotOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import {
  Alert,
  AutoComplete,
  Button,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tabs,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { useIsMobile } from "../hooks/useIsMobile";
import {
  type AutoClassifyRule,
  type DavSearchItem,
  type DavStockIn,
  type DavStockOut,
  type DavStockPatch,
  addDavStock,
  autoClassifyDavStocks,
  fetchDavAutoPayoutRatio,
  fetchDavStocks,
  removeDavStock,
  searchDavStocks,
  updateDavStock,
} from "../api/client";

const { Text, Title } = Typography;

type DavClass = "A" | "B" | "C" | "D";

const CLASS_COLOR: Record<DavClass, string> = {
  A: "green",
  B: "blue",
  C: "orange",
  D: "red",
};

const CLASS_DESC: Record<DavClass, string> = {
  A: "A类：派息稳定 + 盈利稳定（核心持仓）",
  B: "B类：派息稳定 + 盈利周期（逢低布局）",
  C: "C类：派息不稳 + 盈利稳定（关注观察）",
  D: "D类：派息不稳 + 盈利周期（原则回避）",
};

// ── 添加/编辑弹窗 ────────────────────────────────────────────────────────────

interface StockFormValues {
  ts_code: string;
  dav_class: DavClass | null;
  manual_payout_ratio: number | null;
  manual_eps: number | null;
  notes: string | null;
}

interface StockModalProps {
  open: boolean;
  editing: DavStockOut | null;
  onClose: () => void;
  onSaved: () => void;
}

function StockModal({ open, editing, onClose, onSaved }: StockModalProps) {
  const [form] = Form.useForm<StockFormValues>();
  const [saving, setSaving] = useState(false);
  const [autoFetching, setAutoFetching] = useState(false);
  const [searchOpts, setSearchOpts] = useState<{ value: string; label: string }[]>([]);

  useEffect(() => {
    if (open) {
      if (editing) {
        form.setFieldsValue({
          ts_code: editing.ts_code,
          dav_class: editing.dav_class ?? null,
          manual_payout_ratio: editing.manual_payout_ratio ?? null,
          manual_eps: editing.manual_eps ?? null,
          notes: editing.notes ?? null,
        });
      } else {
        form.resetFields();
      }
    }
  }, [open, editing, form]);

  const handleSearch = useCallback(async (q: string) => {
    if (!q) { setSearchOpts([]); return; }
    try {
      const items: DavSearchItem[] = await searchDavStocks(q);
      setSearchOpts(items.map((i) => ({
        value: i.ts_code,
        label: `${i.ts_code}  ${i.name ?? ""}`,
      })));
    } catch {
      setSearchOpts([]);
    }
  }, []);

  const handleAutoFetch = async () => {
    const tsCode = editing?.ts_code ?? (form.getFieldValue("ts_code") as string | undefined);
    if (!tsCode) {
      message.warning("请先选择股票代码");
      return;
    }
    setAutoFetching(true);
    try {
      const result = await fetchDavAutoPayoutRatio(tsCode);
      if (result.payout_ratio == null && result.eps == null) {
        message.warning("未找到分红数据，请手动填写");
        return;
      }
      const updates: Partial<StockFormValues> = {};
      if (result.payout_ratio != null) updates.manual_payout_ratio = parseFloat(result.payout_ratio.toFixed(2));
      if (result.eps != null) updates.manual_eps = parseFloat(result.eps.toFixed(4));
      form.setFieldsValue(updates);
      message.success(
        `已填入：派息率 ${result.payout_ratio != null ? result.payout_ratio.toFixed(2) + "%" : "无"}`
        + `，EPS ${result.eps != null ? "¥" + result.eps.toFixed(4) : "无"}`,
      );
    } catch {
      message.error("自动获取失败，请手动填写");
    } finally {
      setAutoFetching(false);
    }
  };

  const handleOk = async () => {
    let vals: StockFormValues;
    try { vals = await form.validateFields(); } catch { return; }
    setSaving(true);
    try {
      if (editing) {
        const patch: DavStockPatch = {
          dav_class: vals.dav_class ?? undefined,
          manual_payout_ratio: vals.manual_payout_ratio ?? undefined,
          manual_eps: vals.manual_eps ?? undefined,
          notes: vals.notes ?? undefined,
        };
        await updateDavStock(editing.ts_code, patch);
        message.success("已更新");
      } else {
        const body: DavStockIn = {
          ts_code: vals.ts_code,
          dav_class: vals.dav_class ?? undefined,
          manual_payout_ratio: vals.manual_payout_ratio ?? undefined,
          manual_eps: vals.manual_eps ?? undefined,
          notes: vals.notes ?? undefined,
        };
        await addDavStock(body);
        message.success("已添加");
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err?.response?.data?.detail ?? "操作失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={editing ? `编辑 ${editing.ts_code} ${editing.name ?? ""}` : "添加股票"}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      confirmLoading={saving}
      okText={editing ? "保存" : "添加"}
      width={480}
      destroyOnClose
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        {!editing && (
          <Form.Item name="ts_code" label="股票代码" rules={[{ required: true, message: "请选择股票" }]}>
            <AutoComplete
              options={searchOpts}
              onSearch={handleSearch}
              placeholder="输入代码或名称搜索，如 600000 或 浦发"
              allowClear
            />
          </Form.Item>
        )}
        <Form.Item name="dav_class" label="分类">
          <Radio.Group buttonStyle="solid">
            {(["A", "B", "C", "D"] as DavClass[]).map((c) => (
              <Tooltip key={c} title={CLASS_DESC[c]}>
                <Radio.Button value={c}>{c} 类</Radio.Button>
              </Tooltip>
            ))}
          </Radio.Group>
        </Form.Item>

        <div style={{ marginBottom: 12 }}>
          <Button
            icon={<SyncOutlined />}
            loading={autoFetching}
            onClick={handleAutoFetch}
            size="small"
          >
            自动获取派息率和 EPS
          </Button>
          <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>
            从 AKShare 拉取分红历史，约 1-5 秒
          </Text>
        </div>

        <Form.Item
          name="manual_payout_ratio"
          label="近两年平均派息率 %"
          extra={
            editing?.auto_payout_ratio != null && editing.manual_payout_ratio == null
              ? `自动获取参考值：${editing.auto_payout_ratio.toFixed(2)}%（手填后将优先使用）`
              : "例：33.95 表示 33.95%（每年税后分红 ÷ 归母净利润）"
          }
        >
          <InputNumber min={0} max={200} precision={2} style={{ width: "100%" }} placeholder="如 33.95" />
        </Form.Item>
        <Form.Item
          name="manual_eps"
          label="预测全年 EPS（元/股）"
          extra={
            editing?.auto_eps != null && editing.manual_eps == null
              ? `自动获取参考值：¥${editing.auto_eps.toFixed(4)}（手填后将优先使用）`
              : "基于自身判断填写当年或次年预测值"
          }
        >
          <InputNumber precision={4} style={{ width: "100%" }} placeholder="如 1.2345" />
        </Form.Item>
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={2} placeholder="行业基准、大股东诉求、调整依据等" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// ── 自动分类弹窗 ─────────────────────────────────────────────────────────────

interface AutoClassifyModalProps {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}

const DEFAULT_RULES: AutoClassifyRule[] = [
  { yield_min: 5, pe_max: 20, target_class: "A" },
  { yield_min: 3, target_class: "B" },
  { pe_max: 30, target_class: "C" },
  { target_class: "D" },
];

function AutoClassifyModal({ open, onClose, onDone }: AutoClassifyModalProps) {
  const [rules, setRules] = useState<AutoClassifyRule[]>(DEFAULT_RULES);
  const [overwrite, setOverwrite] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [result, setResult] = useState<{ classified: number; skipped: number } | null>(null);

  useEffect(() => {
    if (open) { setRules(DEFAULT_RULES); setOverwrite(false); setResult(null); }
  }, [open]);

  const updateRule = (idx: number, field: keyof AutoClassifyRule, value: unknown) => {
    setRules(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r));
  };

  const addRule = () => setRules(prev => [...prev, { target_class: "D" as const }]);
  const removeRule = (idx: number) => setRules(prev => prev.filter((_, i) => i !== idx));

  const handleRun = async () => {
    if (rules.length === 0) { message.warning("请至少添加一条规则"); return; }
    setClassifying(true);
    try {
      const res = await autoClassifyDavStocks(rules, overwrite);
      setResult({ classified: res.classified, skipped: res.skipped });
      onDone();
    } catch {
      message.error("自动分类失败");
    } finally {
      setClassifying(false);
    }
  };

  return (
    <Modal
      title="自动分类"
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="run" type="primary" loading={classifying} onClick={handleRun} icon={<RobotOutlined />}>
          开始分类
        </Button>,
      ]}
      width={600}
      destroyOnClose
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="规则按顺序从上到下匹配，第一条命中的规则生效。条件为空表示「不限」。"
        />

        {rules.map((rule, idx) => (
          <div key={idx} style={{ padding: "10px 12px", border: "1px solid #f0f0f0", borderRadius: 8 }}>
            <Row gutter={8} align="middle">
              <Col flex="none">
                <Text type="secondary" style={{ fontSize: 12 }}>规则 {idx + 1}</Text>
              </Col>
              <Col flex="auto">
                <Row gutter={6} align="middle" wrap>
                  <Col>
                    <Text style={{ fontSize: 12 }}>股息率 ≥</Text>
                    <InputNumber
                      size="small"
                      min={0}
                      max={100}
                      precision={1}
                      value={rule.yield_min ?? null}
                      onChange={v => updateRule(idx, "yield_min", v ?? undefined)}
                      placeholder="不限"
                      style={{ width: 72, marginLeft: 4 }}
                      addonAfter="%"
                    />
                  </Col>
                  <Col>
                    <Text style={{ fontSize: 12, marginLeft: 4 }}>且 PE ≤</Text>
                    <InputNumber
                      size="small"
                      min={0}
                      precision={0}
                      value={rule.pe_max ?? null}
                      onChange={v => updateRule(idx, "pe_max", v ?? undefined)}
                      placeholder="不限"
                      style={{ width: 72, marginLeft: 4 }}
                    />
                  </Col>
                  <Col>
                    <Text style={{ fontSize: 12, marginLeft: 4 }}>→ 分为</Text>
                    <Select
                      size="small"
                      value={rule.target_class}
                      onChange={v => updateRule(idx, "target_class", v)}
                      style={{ width: 70, marginLeft: 4 }}
                      options={["A", "B", "C", "D"].map(c => ({ value: c, label: `${c} 类` }))}
                    />
                  </Col>
                </Row>
              </Col>
              <Col flex="none">
                <Button
                  size="small"
                  type="text"
                  danger
                  icon={<MinusCircleOutlined />}
                  onClick={() => removeRule(idx)}
                  disabled={rules.length <= 1}
                />
              </Col>
            </Row>
          </div>
        ))}

        <Button size="small" icon={<PlusOutlined />} onClick={addRule}>添加规则</Button>

        <Divider style={{ margin: "8px 0" }} />

        <Row align="middle" gutter={8}>
          <Col>
            <Switch size="small" checked={overwrite} onChange={setOverwrite} />
          </Col>
          <Col>
            <Text style={{ fontSize: 13 }}>覆盖已有分类</Text>
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
              {overwrite ? "所有股票都会重新分类" : "仅对「未分类」股票生效"}
            </Text>
          </Col>
        </Row>

        {result && (
          <Alert
            type="success"
            message={`分类完成：${result.classified} 只已分类，${result.skipped} 只已跳过`}
          />
        )}
      </Space>
    </Modal>
  );
}

// ── 表格列定义 ──────────────────────────────────────────────────────────────

function buildColumns(
  onEdit: (r: DavStockOut) => void,
  onDelete: (tsCode: string) => void,
): ColumnsType<DavStockOut> {
  return [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 110,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 100,
      render: (v: string | null) => v ?? "-",
    },
    {
      title: "分类",
      dataIndex: "dav_class",
      width: 70,
      render: (v: DavClass | null) =>
        v ? <Tag color={CLASS_COLOR[v]}>{v} 类</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: "当前价",
      dataIndex: "latest_price",
      width: 90,
      align: "right",
      render: (v: number | null) =>
        v != null ? <Text>{v.toFixed(2)}</Text> : <Text type="secondary">无行情</Text>,
    },
    {
      title: "派息率 %",
      width: 110,
      align: "right",
      render: (_: unknown, record: DavStockOut) => {
        if (record.manual_payout_ratio != null)
          return `${record.manual_payout_ratio.toFixed(2)}%`;
        if (record.auto_payout_ratio != null)
          return (
            <span>
              {record.auto_payout_ratio.toFixed(2)}%
              <Text type="secondary" style={{ fontSize: 11, marginLeft: 3 }}>(自动)</Text>
            </span>
          );
        return <Text type="warning">待补充</Text>;
      },
    },
    {
      title: "预测 EPS",
      width: 110,
      align: "right",
      render: (_: unknown, record: DavStockOut) => {
        if (record.manual_eps != null)
          return `¥${record.manual_eps.toFixed(4)}`;
        if (record.auto_eps != null)
          return (
            <span>
              ¥{record.auto_eps.toFixed(4)}
              <Text type="secondary" style={{ fontSize: 11, marginLeft: 3 }}>(自动)</Text>
            </span>
          );
        return <Text type="warning">待补充</Text>;
      },
    },
    {
      title: "预期股息率",
      dataIndex: "expected_yield",
      width: 110,
      align: "right",
      sorter: (a: DavStockOut, b: DavStockOut) =>
        (a.expected_yield ?? -Infinity) - (b.expected_yield ?? -Infinity),
      render: (v: number | null, record: DavStockOut) => {
        if (!record.data_complete) {
          return (
            <Tooltip title="派息率或 EPS 尚未填写，无法计算">
              <Text type="secondary">
                <ExclamationCircleOutlined style={{ marginRight: 4 }} />
                数据不全
              </Text>
            </Tooltip>
          );
        }
        if (v == null) return <Text type="secondary">-</Text>;
        const color = v >= 6 ? "#52c41a" : v >= 4 ? "#1677ff" : "#faad14";
        return (
          <Tooltip title="派息率% × EPS ÷ 当前价 × 100">
            <Text strong style={{ color }}>{v.toFixed(2)}%</Text>
          </Tooltip>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "data_complete",
      width: 80,
      render: (v: boolean) =>
        v ? (
          <CheckCircleOutlined style={{ color: "#52c41a" }} />
        ) : (
          <Tooltip title="需补充派息率和 EPS 后方可计算预期股息率">
            <ExclamationCircleOutlined style={{ color: "#faad14" }} />
          </Tooltip>
        ),
    },
    {
      title: "备注",
      dataIndex: "notes",
      ellipsis: true,
      render: (v: string | null) => v ? <Tooltip title={v}><Text ellipsis>{v}</Text></Tooltip> : null,
    },
    {
      title: "操作",
      width: 100,
      render: (_: unknown, record: DavStockOut) => (
        <Space>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => onEdit(record)}
          />
          <Popconfirm
            title={`确认从看板移除 ${record.ts_code}？`}
            onConfirm={() => onDelete(record.ts_code)}
            okText="移除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" icon={<DeleteOutlined />} danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];
}

// ── 主页面 ──────────────────────────────────────────────────────────────────

const TAB_ITEMS = [
  { key: "all", label: "全部" },
  { key: "A",   label: "A 类" },
  { key: "B",   label: "B 类" },
  { key: "C",   label: "C 类" },
  { key: "D",   label: "D 类" },
  { key: "none", label: "未分类" },
];

export default function DavPage() {
  const [stocks, setStocks] = useState<DavStockOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<DavStockOut | null>(null);
  const [classifyOpen, setClassifyOpen] = useState(false);
  const isMobile = useIsMobile();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchDavStocks();
      setStocks(data);
    } catch {
      message.error("加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleEdit = (record: DavStockOut) => {
    setEditTarget(record);
    setModalOpen(true);
  };

  const handleDelete = async (tsCode: string) => {
    try {
      await removeDavStock(tsCode);
      message.success("已移除");
      load();
    } catch {
      message.error("移除失败");
    }
  };

  const handleAdd = () => {
    setEditTarget(null);
    setModalOpen(true);
  };

  const columns = buildColumns(handleEdit, handleDelete);

  const filtered = stocks.filter((s) => {
    if (tab === "all") return true;
    if (tab === "none") return s.dav_class == null;
    return s.dav_class === tab;
  });

  return (
    <div style={{ width: "100%", padding: isMobile ? "0 4px" : 0 }}>
      <div style={{ marginBottom: 16, display: "flex", alignItems: "baseline", gap: 12 }}>
        <Title level={4} style={{ margin: 0 }}>大V看板</Title>
        <Text type="secondary" style={{ fontSize: 12 }}>
          基于 Mr. Dang 四分类框架，追踪各类股票预期股息率
        </Text>
      </div>

      <div style={{ marginBottom: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["A", "B", "C", "D"] as DavClass[]).map((c) => (
          <Tag key={c} color={CLASS_COLOR[c]} style={{ fontSize: 12 }}>{CLASS_DESC[c]}</Tag>
        ))}
      </div>

      <div style={{ marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Tabs
          items={TAB_ITEMS}
          activeKey={tab}
          onChange={setTab}
          style={{ marginBottom: 0 }}
          size="small"
        />
        <Space>
          <Button icon={<RobotOutlined />} onClick={() => setClassifyOpen(true)}>
            自动分类
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
            添加股票
          </Button>
        </Space>
      </div>

      <Table<DavStockOut>
        rowKey="ts_code"
        dataSource={filtered}
        columns={columns}
        loading={loading}
        size="small"
        pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{ emptyText: tab === "all" ? "看板暂无股票，点击「添加股票」开始" : `暂无 ${tab} 类股票` }}
      />

      <div style={{ marginTop: 12 }}>
        <Text type="secondary" style={{ fontSize: 11 }}>
          预期股息率 = 派息率% × 预测EPS ÷ 当前价 × 100。当前价取本地日线最新收盘价，无行情时显示"无行情"。
        </Text>
      </div>

      <StockModal
        open={modalOpen}
        editing={editTarget}
        onClose={() => setModalOpen(false)}
        onSaved={load}
      />

      <AutoClassifyModal
        open={classifyOpen}
        onClose={() => setClassifyOpen(false)}
        onDone={load}
      />
    </div>
  );
}
