/**
 * 指标库页面
 *
 * 功能：管理所有技术指标，分为两大类：
 * - 内置指标（Builtin）：系统预置，如 MA、MACD、KDJ、BOLL 等，只可查看
 * - 自定义指标（Custom）：用户自己定义，可新建、编辑、删除
 *
 * 自定义指标支持两种形式：
 * - DSL 模式（推荐）：通过可视化构建器配置多参数、多子线、公式树
 * - 旧版表达式（Legacy）：单行 Python 风格表达式，功能受限
 *
 * 保存前必须用一只股票做「试算」，确保公式正确才能保存。
 */
import { ArrowLeftOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Card, Collapse, Descriptions, Form, Input, Modal, Radio, Space, Table, Tabs, Tag, Typography, message, theme } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  UserIndicatorBuilder,
  emptyDefinition,
  type UserIndicatorDefinitionDraft,
} from "../components/UserIndicatorBuilder";
import {
  createCustomIndicator,
  deleteCustomIndicator,
  fetchBuiltinIndicatorCatalog,
  fetchCustomIndicatorVariableNames,
  fetchCustomIndicators,
  fetchIndicatorDetail,
  fetchIndicators,
  getApiErrorMessage,
  patchCustomIndicator,
  validateCustomIndicatorDefinition,
  validateCustomIndicatorExpr,
  validateSavedCustomIndicator,
  type IndicatorDetail,
  type IndicatorListItem,
  type UserIndicatorOut,
  type UserIndicatorValidateOut,
} from "../api/client";
import { zebraRowClass } from "../constants/theme";

const { Text } = Typography;
const { TextArea } = Input;

/**
 * 把后端返回的指标定义 JSON 规范化为前端 Draft 格式
 * 补全缺失字段的默认值，确保构建器组件可以正常渲染
 *
 * @param d - 后端原始定义对象
 * @returns 标准化后的 UserIndicatorDefinitionDraft
 */
function normalizeDefinition(d: Record<string, unknown>): UserIndicatorDefinitionDraft {
  const subs = (d.sub_indicators as Record<string, unknown>[]) || [];
  return {
    version: 1,
    params: ((d.params as UserIndicatorDefinitionDraft["params"]) || []).map((p) => ({
      name: p.name || "N",
      description: p.description || "",
      default_value: p.default_value ?? "",
    })),
    periods: (d.periods as string[])?.length ? (d.periods as string[]) : ["1d"],
    sub_indicators: subs.length
      ? subs.map((s) => ({
          key: String(s.key || "main"),
          name: String(s.name || ""),
          description: String(s.description || ""),
          auxiliary_only: Boolean(s.auxiliary_only),
          use_in_screening: s.use_in_screening !== false,
          use_in_chart: s.use_in_chart !== false,
          chart_kind: (s.chart_kind as "line" | "bar" | null) || "line",
          initial_value: (s.initial_value as string | null) ?? null,
          formula: (s.formula as Record<string, unknown>) || { op: "intrinsic", field: "close" },
        }))
      : emptyDefinition().sub_indicators,
  };
}

export default function IndicatorLibPage() {
  const { token } = theme.useToken();
  const [mainTab, setMainTab] = useState<"builtin" | "custom">("builtin");

  const [list, setList] = useState<IndicatorListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<IndicatorDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [customList, setCustomList] = useState<UserIndicatorOut[]>([]);
  const [customLoading, setCustomLoading] = useState(false);
  const [varNames, setVarNames] = useState<string[]>([]);
  const [builtinCatalog, setBuiltinCatalog] = useState<Awaited<ReturnType<typeof fetchBuiltinIndicatorCatalog>>>([]);
  const flatBuiltinSubs = useMemo(
    () => builtinCatalog.flatMap((c) => c.subs.map((s) => s.name)),
    [builtinCatalog],
  );

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<UserIndicatorOut | null>(null);
  const [editorMode, setEditorMode] = useState<"dsl" | "legacy">("dsl");
  const [definitionDraft, setDefinitionDraft] = useState<UserIndicatorDefinitionDraft>(() => emptyDefinition());
  const [form] = Form.useForm<{ code: string; display_name: string; description?: string; expr: string; ts_code: string }>();
  const [validateResult, setValidateResult] = useState<UserIndicatorValidateOut | null>(null);
  const [validating, setValidating] = useState(false);

  const refreshBuiltin = useCallback(async () => {
    setLoading(true);
    try {
      setList(await fetchIndicators());
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshCustom = useCallback(async () => {
    setCustomLoading(true);
    try {
      setCustomList(await fetchCustomIndicators());
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setCustomLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshBuiltin();
  }, [refreshBuiltin]);

  useEffect(() => {
    if (mainTab === "custom") {
      void refreshCustom();
      void (async () => {
        try {
          const o = await fetchCustomIndicatorVariableNames();
          setVarNames(o.names);
        } catch {
          setVarNames([]);
        }
        try {
          setBuiltinCatalog(await fetchBuiltinIndicatorCatalog());
        } catch {
          setBuiltinCatalog([]);
        }
      })();
    }
  }, [mainTab, refreshCustom]);

  const openDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      setDetail(await fetchIndicatorDetail(id));
    } finally {
      setDetailLoading(false);
    }
  };

  /* 试算表：附 diagnostics 简要展示 */
  const sampleCols: ColumnsType<NonNullable<UserIndicatorValidateOut["sample_rows"]>[0]> = useMemo(() => {
    const diagRender = (
      d: NonNullable<UserIndicatorValidateOut["sample_rows"]>[0]["diagnostics"],
    ) =>
      d?.length ? d.map((x) => `${x.code ?? ""}${x.detail ? `: ${x.detail}` : ""}`).join("；") : "—";
    const keys = validateResult?.report_keys?.length
      ? validateResult.report_keys
      : validateResult?.sample_rows?.[0]?.values
        ? Object.keys(validateResult.sample_rows[0].values || {})
        : [];
    if (keys.length) {
      return [
        { title: "交易日", dataIndex: "trade_date", width: 120 },
        ...keys.map((k) => ({
          title: k,
          key: k,
          render: (_: unknown, r: NonNullable<UserIndicatorValidateOut["sample_rows"]>[0]) => {
            const x = r.values?.[k];
            return x == null ? "—" : Number(x).toFixed(6);
          },
        })),
        { title: "错误", dataIndex: "error", ellipsis: true, render: (e: string | null) => e ?? "—" },
        { title: "诊断", dataIndex: "diagnostics", ellipsis: true, render: diagRender },
      ];
    }
    return [
      { title: "交易日", dataIndex: "trade_date" },
      { title: "值", dataIndex: "value", render: (v: number | null | undefined) => (v == null ? "—" : v.toFixed(6)) },
      { title: "错误", dataIndex: "error", render: (e: string | null) => e ?? "—" },
      { title: "诊断", dataIndex: "diagnostics", ellipsis: true, render: diagRender },
    ];
  }, [validateResult]);

  const openCreateModal = () => {
    setEditing(null);
    setValidateResult(null);
    setEditorMode("dsl");
    setDefinitionDraft(emptyDefinition());
    form.resetFields();
    form.setFieldsValue({ ts_code: "600000.SH", code: "", display_name: "", expr: "" });
    setModalOpen(true);
  };

  const openEditModal = (row: UserIndicatorOut) => {
    setEditing(row);
    setValidateResult(null);
    if (row.kind === "dsl" && row.definition) {
      setEditorMode("dsl");
      setDefinitionDraft(normalizeDefinition(row.definition));
    } else {
      setEditorMode("legacy");
      setDefinitionDraft(emptyDefinition());
    }
    form.setFieldsValue({
      code: row.code,
      display_name: row.display_name,
      description: row.description ?? "",
      expr: row.expr ?? "",
      ts_code: "600000.SH",
    });
    setModalOpen(true);
  };

  const runValidate = async () => {
    const ts_code = (form.getFieldValue("ts_code") || "").trim();
    if (!ts_code) {
      message.warning("请填写验证用股票代码");
      return;
    }
    setValidating(true);
    setValidateResult(null);
    try {
      let out: UserIndicatorValidateOut;
      if (editing) {
        out = await validateSavedCustomIndicator(editing.id, { ts_code });
      } else if (editorMode === "dsl") {
        out = await validateCustomIndicatorDefinition({
          definition: { ...definitionDraft } as unknown as Record<string, unknown>,
          ts_code,
        });
      } else {
        const expr = form.getFieldValue("expr");
        if (!expr?.trim()) {
          message.warning("请填写表达式");
          setValidating(false);
          return;
        }
        out = await validateCustomIndicatorExpr({ expr: expr.trim(), ts_code });
      }
      setValidateResult(out);
      if (out.ok) message.success(out.message);
      else message.warning(out.message);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setValidating(false);
    }
  };

  const submitModal = async () => {
    try {
      const v = await form.validateFields();
      const trial = (v.ts_code || "600000.SH").trim();
      if (editing) {
        if (editorMode === "dsl") {
          await patchCustomIndicator(editing.id, {
            display_name: v.display_name,
            description: v.description || null,
            definition: { ...definitionDraft } as unknown as Record<string, unknown>,
            trial_ts_code: trial,
          });
        } else {
          await patchCustomIndicator(editing.id, {
            display_name: v.display_name,
            description: v.description || null,
            expr: (v.expr || "").trim(),
            trial_ts_code: trial,
          });
        }
        message.success("已更新");
      } else if (editorMode === "dsl") {
        await createCustomIndicator({
          code: v.code.trim(),
          display_name: v.display_name.trim(),
          description: v.description?.trim() || null,
          definition: { ...definitionDraft } as unknown as Record<string, unknown>,
          trial_ts_code: trial,
        });
        message.success("已创建");
      } else {
        await createCustomIndicator({
          code: v.code.trim(),
          display_name: v.display_name.trim(),
          description: v.description?.trim() || null,
          expr: (v.expr || "").trim(),
          trial_ts_code: trial,
        });
        message.success("已创建");
      }
      setModalOpen(false);
      void refreshCustom();
    } catch (e) {
      if (e && typeof e === "object" && "errorFields" in e) return;
      message.error(getApiErrorMessage(e));
    }
  };

  const removeCustom = (row: UserIndicatorOut) => {
    Modal.confirm({
      title: `删除自定义指标「${row.display_name}」？`,
      okType: "danger",
      onOk: async () => {
        try {
          await deleteCustomIndicator(row.id);
          message.success("已删除");
          void refreshCustom();
        } catch (e) {
          message.error(getApiErrorMessage(e));
        }
      },
    });
  };

  const listColumns: ColumnsType<IndicatorListItem> = [
    {
      title: "指标名称",
      dataIndex: "display_name",
      width: 160,
      render: (v, r) => <Typography.Link onClick={() => void openDetail(r.id)}>{v}</Typography.Link>,
    },
    { title: "英文标识", dataIndex: "name", width: 120, render: (v: string) => <Tag>{v}</Tag> },
    { title: "描述", dataIndex: "description", ellipsis: true },
    { title: "参数数", dataIndex: "params_count", width: 80, align: "center" },
    { title: "子指标数", dataIndex: "sub_count", width: 90, align: "center" },
    {
      title: "操作",
      key: "action",
      width: 80,
      align: "center",
      render: (_, r) => <Button size="small" onClick={() => void openDetail(r.id)}>详情</Button>,
    },
  ];

  const customColumns: ColumnsType<UserIndicatorOut> = [
    {
      title: "类型",
      dataIndex: "kind",
      width: 72,
      render: (k: string) => (
        <Tag color={k === "dsl" ? "blue" : "default"}>{k === "dsl" ? "DSL" : "旧版"}</Tag>
      ),
    },
    { title: "code", dataIndex: "code", width: 120, render: (v) => <Tag color="blue">{v}</Tag> },
    { title: "名称", dataIndex: "display_name", width: 140 },
    {
      title: "摘要",
      key: "sum",
      ellipsis: true,
      render: (_, r) => {
        if (r.kind === "dsl" && r.definition?.sub_indicators && Array.isArray(r.definition.sub_indicators)) {
          const subs = r.definition.sub_indicators as { name?: string }[];
          return subs.map((s) => s.name).filter(Boolean).join("、") || "—";
        }
        return r.expr || "—";
      },
    },
    {
      title: "操作",
      key: "op",
      width: 200,
      render: (_, r) => (
        <Space size="small">
          <Button size="small" icon={<EditOutlined />} onClick={() => openEditModal(r)}>
            编辑
          </Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => removeCustom(r)}>
            删除
          </Button>
        </Space>
      ),
    },
  ];

  if (detail && mainTab === "builtin") {
    const paramCols: ColumnsType<IndicatorDetail["params"][0]> = [
      { title: "参数名", dataIndex: "name", width: 120 },
      { title: "说明", dataIndex: "description", ellipsis: true },
      { title: "默认值", dataIndex: "default_value", width: 100, render: (v: string | null) => v ?? "-" },
    ];
    const subCols: ColumnsType<IndicatorDetail["sub_indicators"][0]> = [
      { title: "子指标名", dataIndex: "name", width: 160 },
      { title: "说明", dataIndex: "description", ellipsis: true },
      {
        title: "可作为买入/卖出价格",
        dataIndex: "can_be_price",
        width: 150,
        align: "center",
        render: (v: boolean) => (v ? <Tag color="green">Y</Tag> : <Tag color="default">N</Tag>),
      },
    ];

    return (
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => setDetail(null)}>返回列表</Button>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {detail.display_name}（{detail.name}）
          </Typography.Title>
        </Space>
        <Card title="基本信息">
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="指标名称">{detail.display_name}</Descriptions.Item>
            <Descriptions.Item label="英文标识"><Tag>{detail.name}</Tag></Descriptions.Item>
            <Descriptions.Item label="描述">{detail.description ?? "-"}</Descriptions.Item>
          </Descriptions>
        </Card>
        <Card title={`参数信息（共 ${detail.params.length} 个）`}>
          {detail.params.length === 0 ? (
            <Typography.Text type="secondary">该指标无参数</Typography.Text>
          ) : (
            <Table rowKey="id" columns={paramCols} dataSource={detail.params} pagination={false} size="small" />
          )}
        </Card>
        <Card title={`子指标（共 ${detail.sub_indicators.length} 个）`}>
          <Table rowKey="id" columns={subCols} dataSource={detail.sub_indicators} pagination={false} size="small" />
        </Card>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>指标库</Typography.Title>
      <Tabs
        activeKey={mainTab}
        onChange={(k) => {
          setMainTab(k as "builtin" | "custom");
          setDetail(null);
        }}
        items={[
          {
            key: "builtin",
            label: "内置指标",
            children: (
              <Card loading={detailLoading}>
                <Table
                  rowKey="id"
                  loading={loading}
                  columns={listColumns}
                  dataSource={list}
                  pagination={false}
                  size="middle"
                  rowClassName={zebraRowClass}
                  onRow={(r) => ({ onClick: () => void openDetail(r.id), style: { cursor: "pointer" } })}
                />
              </Card>
            ),
          },
          {
            key: "custom",
            label: "自定义指标",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Space wrap>
                  <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>新建</Button>
                  <Text type="secondary">
                    支持多参数、多子线与公式树；保存前需用一只股票验证公式正确性。
                  </Text>
                </Space>
                <Collapse
                  items={[
                    {
                      key: "vars",
                      label: `单行表达式指标可用变量（共 ${varNames.length} 个）`,
                      children: (
                        <div style={{ maxHeight: 160, overflow: "auto", fontSize: 12, color: token.colorTextSecondary }}>
                          {varNames.join("、")}
                        </div>
                      ),
                    },
                  ]}
                />
                <Card>
                  <Table rowKey="id" loading={customLoading} columns={customColumns} dataSource={customList} pagination={false} rowClassName={zebraRowClass} />
                </Card>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? "编辑自定义指标" : "新建自定义指标"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void submitModal()}
        width={920}
        destroyOnClose
        styles={{ body: { maxHeight: "75vh", overflow: "auto" } }}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="code" label="code（英文标识，创建后不可改）" rules={[{ required: !editing, message: "必填" }]}>
            <Input placeholder="如 my_ma_diff" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称" rules={[{ required: true, message: "必填" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input />
          </Form.Item>
          {!editing && (
            <Form.Item label="编辑形式">
              <Radio.Group value={editorMode} onChange={(e) => setEditorMode(e.target.value)}>
                <Radio.Button value="dsl">可视化构建器</Radio.Button>
                <Radio.Button value="legacy">单行表达式</Radio.Button>
              </Radio.Group>
            </Form.Item>
          )}
          {editing?.kind === "legacy" && (
            <Typography.Paragraph type="warning">
              当前指标使用单行表达式，不支持多子线；如需完整功能，请新建一条指标。
            </Typography.Paragraph>
          )}
          {editorMode === "dsl" && (
            <UserIndicatorBuilder
              value={definitionDraft}
              onChange={setDefinitionDraft}
              builtinSubNames={flatBuiltinSubs}
            />
          )}
          {editorMode === "legacy" && (
            <Form.Item name="expr" label="表达式" rules={[{ required: true, message: "必填" }]}>
              <TextArea rows={4} placeholder="例：(close - MA20) / MA20 * 100" />
            </Form.Item>
          )}
          <Form.Item name="ts_code" label="验证用股票代码" extra="填写一只已同步数据的股票代码，用于保存前验证公式（如 600000.SH）">
            <Input placeholder="600000.SH" />
          </Form.Item>
          <Space>
            <Button onClick={() => void runValidate()} loading={validating}>试算</Button>
          </Space>
          {validateResult && (
            <Card size="small" style={{ marginTop: 12 }} title="试算结果">
              <Text type={validateResult.ok ? "success" : "warning"}>{validateResult.message}</Text>
              {validateResult.sample_rows.length > 0 && (
                <Table
                  size="small"
                  style={{ marginTop: 8 }}
                  rowKey="trade_date"
                  pagination={false}
                  dataSource={validateResult.sample_rows}
                  columns={sampleCols}
                />
              )}
            </Card>
          )}
        </Form>
      </Modal>
    </Space>
  );
}
