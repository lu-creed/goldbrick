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
  PlusOutlined,
} from "@ant-design/icons";
import {
  AutoComplete,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Space,
  Table,
  Tag,
  Tabs,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import {
  DavSearchItem,
  DavStockIn,
  DavStockOut,
  DavStockPatch,
  addDavStock,
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
  editing: DavStockOut | null;   // null = 添加模式
  onClose: () => void;
  onSaved: () => void;
}

function StockModal({ open, editing, onClose, onSaved }: StockModalProps) {
  const [form] = Form.useForm<StockFormValues>();
  const [saving, setSaving] = useState(false);
  const [searchOpts, setSearchOpts] = useState<{ value: string; label: string }[]>([]);

  // 编辑模式：填充已有数据
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
        <Form.Item
          name="manual_payout_ratio"
          label="近两年平均派息率 %"
          extra="例：33.95 表示 33.95%（每年税后分红 ÷ 归母净利润）"
        >
          <InputNumber min={0} max={200} precision={2} style={{ width: "100%" }} placeholder="如 33.95" />
        </Form.Item>
        <Form.Item
          name="manual_eps"
          label="预测全年 EPS（元/股）"
          extra="基于自身判断填写当年或次年预测值"
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
      dataIndex: "manual_payout_ratio",
      width: 90,
      align: "right",
      render: (v: number | null) =>
        v != null ? `${v.toFixed(2)}%` : <Text type="warning">待补充</Text>,
    },
    {
      title: "预测 EPS",
      dataIndex: "manual_eps",
      width: 90,
      align: "right",
      render: (v: number | null) =>
        v != null ? `¥${v.toFixed(4)}` : <Text type="warning">待补充</Text>,
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
    <div style={{ maxWidth: 1200, margin: "0 auto" }}>
      {/* 页头 */}
      <div style={{ marginBottom: 16, display: "flex", alignItems: "baseline", gap: 12 }}>
        <Title level={4} style={{ margin: 0 }}>大V看板</Title>
        <Text type="secondary" style={{ fontSize: 12 }}>
          基于 Mr. Dang 四分类框架，追踪各类股票预期股息率
        </Text>
      </div>

      {/* 分类说明 */}
      <div style={{ marginBottom: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["A", "B", "C", "D"] as DavClass[]).map((c) => (
          <Tag key={c} color={CLASS_COLOR[c]} style={{ fontSize: 12 }}>{CLASS_DESC[c]}</Tag>
        ))}
      </div>

      {/* 操作栏 */}
      <div style={{ marginBottom: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Tabs
          items={TAB_ITEMS}
          activeKey={tab}
          onChange={setTab}
          style={{ marginBottom: 0 }}
          size="small"
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加股票
        </Button>
      </div>

      {/* 数据表 */}
      <Table<DavStockOut>
        rowKey="ts_code"
        dataSource={filtered}
        columns={columns}
        loading={loading}
        size="small"
        pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{ emptyText: tab === "all" ? "看板暂无股票，点击「添加股票」开始" : `暂无 ${tab} 类股票` }}
      />

      {/* 底部提示 */}
      <div style={{ marginTop: 12 }}>
        <Text type="secondary" style={{ fontSize: 11 }}>
          预期股息率 = 派息率% × 预测EPS ÷ 当前价 × 100。当前价取本地日线最新收盘价，无行情时显示"无行情"。
        </Text>
      </div>

      {/* 添加/编辑弹窗 */}
      <StockModal
        open={modalOpen}
        editing={editTarget}
        onClose={() => setModalOpen(false)}
        onSaved={load}
      />
    </div>
  );
}
