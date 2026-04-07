/**
 * PRD 自定义指标：参数列表 + 多子线 + 公式树（与后端 user_indicator_dsl 字段一致）。
 */
import { Button, Card, Input, InputNumber, Radio, Select, Space, Switch, Typography } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { useMemo, type ReactNode } from "react";

export type FormulaNode = Record<string, unknown>;

export type SubIndicatorDraft = {
  key: string;
  name: string;
  description: string;
  auxiliary_only: boolean;
  use_in_screening: boolean;
  use_in_chart: boolean;
  chart_kind: "line" | "bar" | null;
  initial_value: string | null;
  formula: FormulaNode;
};

export type UserIndicatorDefinitionDraft = {
  version: 1;
  params: { name: string; description: string; default_value: string }[];
  periods: string[];
  sub_indicators: SubIndicatorDraft[];
};

const PERIOD_OPTS = [
  { value: "1d", label: "日线" },
  { value: "1w", label: "周线" },
  { value: "1M", label: "月线" },
  { value: "1Q", label: "季线" },
  { value: "1y", label: "年线" },
];

const INTRINSIC_OPTS = [
  { value: "close", label: "收盘价" },
  { value: "open", label: "开盘价" },
  { value: "high", label: "最高价" },
  { value: "low", label: "最低价" },
  { value: "volume", label: "成交量" },
  { value: "amount", label: "成交额" },
  { value: "turnover_rate", label: "换手率" },
];

const OP_OPTS = [
  { value: "intrinsic", label: "固有行情" },
  { value: "rolling", label: "滚动统计(N周期)" },
  { value: "param", label: "本指标参数" },
  { value: "num", label: "数字" },
  { value: "add", label: "加 +" },
  { value: "sub", label: "减 -" },
  { value: "mul", label: "乘 ×" },
  { value: "div", label: "除 ÷" },
  { value: "sqrt", label: "开方 sqrt" },
  { value: "neg", label: "取负 -" },
  { value: "ref_builtin", label: "引用内置子线" },
  { value: "ref_sibling", label: "引用本指标子线" },
];

function defaultFormulaForOp(op: string): FormulaNode {
  switch (op) {
    case "intrinsic":
      return { op: "intrinsic", field: "close" };
    case "rolling":
      return { op: "rolling", field: "close", n_param: "N", stat: "avg" };
    case "param":
      return { op: "param", name: "N" };
    case "num":
      return { op: "num", value: 0 };
    case "add":
      return { op: "add", left: { op: "intrinsic", field: "close" }, right: { op: "num", value: 0 } };
    case "sub":
      return { op: "sub", left: { op: "intrinsic", field: "close" }, right: { op: "num", value: 0 } };
    case "mul":
      return { op: "mul", left: { op: "intrinsic", field: "close" }, right: { op: "num", value: 1 } };
    case "div":
      return { op: "div", left: { op: "intrinsic", field: "close" }, right: { op: "num", value: 1 } };
    case "sqrt":
      return { op: "sqrt", x: { op: "intrinsic", field: "close" } };
    case "neg":
      return { op: "neg", x: { op: "intrinsic", field: "close" } };
    case "ref_builtin":
      return { op: "ref_builtin", sub_name: "MA5", fetch: { mode: "current" } };
    case "ref_sibling":
      return { op: "ref_sibling", sub_key: "main", fetch: { mode: "current" } };
    default:
      return { op: "intrinsic", field: "close" };
  }
}

export function emptyDefinition(): UserIndicatorDefinitionDraft {
  return {
    version: 1,
    params: [{ name: "N", description: "示例周期", default_value: "5" }],
    periods: ["1d"],
    sub_indicators: [
      {
        key: "main",
        name: "主线",
        description: "",
        auxiliary_only: false,
        use_in_screening: true,
        use_in_chart: true,
        chart_kind: "line",
        initial_value: null,
        formula: { op: "intrinsic", field: "close" },
      },
    ],
  };
}

function FetchEditor({
  value,
  onChange,
  paramNames,
  formulaSlot,
}: {
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  paramNames: string[];
  formulaSlot: (node: FormulaNode, upd: (n: FormulaNode) => void) => ReactNode;
}) {
  const mode = (value.mode as string) || "current";
  const poptions = paramNames.map((p) => ({ value: p, label: p }));
  return (
    <Card size="small" title="取数方式" style={{ marginTop: 8 }}>
      <Radio.Group
        value={mode}
        onChange={(e) => {
          const m = e.target.value;
          if (m === "current") onChange({ mode: "current" });
          else if (m === "prev_n") onChange({ mode: "prev_n", n_param: paramNames[0] || "N" });
          else onChange({ mode: "range", n_param: paramNames[0] || "N", range_agg: "avg" });
        }}
      >
        <Radio.Button value="current">当前周期</Radio.Button>
        <Radio.Button value="prev_n">前 N 周期</Radio.Button>
        <Radio.Button value="range">区间</Radio.Button>
      </Radio.Group>
      {(mode === "prev_n" || mode === "range") && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">N 来自参数 </Typography.Text>
          <Select
            style={{ minWidth: 120 }}
            value={(value.n_param as string) || paramNames[0]}
            options={poptions}
            onChange={(n) => onChange({ ...value, n_param: n })}
          />
        </div>
      )}
      {mode === "range" && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">聚合 </Typography.Text>
          <Select
            style={{ minWidth: 120 }}
            value={(value.range_agg as string) || "avg"}
            options={[
              { value: "avg", label: "平均" },
              { value: "min", label: "最低" },
              { value: "max", label: "最高" },
              { value: "std", label: "标准差" },
            ]}
            onChange={(agg) => {
              if (agg === "std") {
                onChange({
                  ...value,
                  mode: "range",
                  range_agg: agg,
                  std_baseline: { op: "intrinsic", field: "close" },
                  std_volatility: "close",
                });
              } else {
                const { std_baseline: _b, std_volatility: _v, ...rest } = value;
                onChange({ ...rest, mode: "range", range_agg: agg });
              }
            }}
          />
        </div>
      )}
      {mode === "range" && value.range_agg === "std" && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">基准</Typography.Text>
          {formulaSlot((value.std_baseline as FormulaNode) || { op: "intrinsic", field: "close" }, (n) =>
            onChange({ ...value, std_baseline: n }),
          )}
          <div style={{ marginTop: 8 }}>
            <Typography.Text type="secondary">波动（仅固有字段） </Typography.Text>
            <Select
              style={{ minWidth: 140 }}
              value={(value.std_volatility as string) || "close"}
              options={INTRINSIC_OPTS}
              onChange={(f) => onChange({ ...value, std_volatility: f })}
            />
          </div>
        </div>
      )}
    </Card>
  );
}

export function FormulaEditor({
  value,
  onChange,
  paramNames,
  subKeys,
  builtinSubNames,
  selfSubKey,
}: {
  value: FormulaNode;
  onChange: (v: FormulaNode) => void;
  paramNames: string[];
  subKeys: string[];
  builtinSubNames: string[];
  selfSubKey?: string;
}) {
  const op = (value?.op as string) || "intrinsic";
  const siblingChoices = subKeys.filter((k) => k !== selfSubKey);

  const wrapSlot = (node: FormulaNode, upd: (n: FormulaNode) => void) => (
    <FormulaEditor
      value={node}
      onChange={upd}
      paramNames={paramNames}
      subKeys={subKeys}
      builtinSubNames={builtinSubNames}
      selfSubKey={selfSubKey}
    />
  );

  return (
    <Card size="small" style={{ background: "#fafafa" }}>
      <Space wrap>
        <Typography.Text type="secondary">节点</Typography.Text>
        <Select
          style={{ minWidth: 160 }}
          value={op}
          options={OP_OPTS}
          onChange={(o) => onChange(defaultFormulaForOp(o))}
        />
      </Space>
      {op === "intrinsic" && (
        <div style={{ marginTop: 8 }}>
          <Select
            style={{ width: "100%", maxWidth: 280 }}
            value={value.field as string}
            options={INTRINSIC_OPTS}
            onChange={(f) => onChange({ op: "intrinsic", field: f })}
          />
        </div>
      )}
      {op === "rolling" && (
        <Space wrap style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">字段</Typography.Text>
          <Select
            style={{ width: 140 }}
            value={value.field as string}
            options={INTRINSIC_OPTS}
            onChange={(f) => onChange({ ...value, op: "rolling", field: f })}
          />
          <Typography.Text type="secondary">N←参数</Typography.Text>
          <Select
            style={{ width: 120 }}
            value={value.n_param as string}
            options={paramNames.map((p) => ({ value: p, label: p }))}
            onChange={(n) => onChange({ ...value, op: "rolling", n_param: n })}
          />
          <Typography.Text type="secondary">统计</Typography.Text>
          <Select
            style={{ width: 100 }}
            value={(value.stat as string) || "avg"}
            options={[
              { value: "avg", label: "均值" },
              { value: "min", label: "最低" },
              { value: "max", label: "最高" },
            ]}
            onChange={(s) => onChange({ ...value, op: "rolling", stat: s })}
          />
        </Space>
      )}
      {op === "param" && (
        <div style={{ marginTop: 8 }}>
          <Select
            style={{ width: "100%", maxWidth: 200 }}
            value={value.name as string}
            options={paramNames.map((p) => ({ value: p, label: p }))}
            onChange={(n) => onChange({ op: "param", name: n })}
          />
        </div>
      )}
      {op === "num" && (
        <div style={{ marginTop: 8 }}>
          <InputNumber
            value={value.value as number}
            onChange={(n) => onChange({ op: "num", value: n ?? 0 })}
            style={{ width: "100%" }}
          />
        </div>
      )}
      {op === "sqrt" && <div style={{ marginTop: 8 }}>{wrapSlot((value.x as FormulaNode) || { op: "intrinsic", field: "close" }, (n) => onChange({ op: "sqrt", x: n }))}</div>}
      {op === "neg" && <div style={{ marginTop: 8 }}>{wrapSlot((value.x as FormulaNode) || { op: "intrinsic", field: "close" }, (n) => onChange({ op: "neg", x: n }))}</div>}
      {(op === "add" || op === "sub" || op === "mul" || op === "div") && (
        <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
          {wrapSlot((value.left as FormulaNode) || { op: "intrinsic", field: "close" }, (n) => onChange({ ...value, left: n, op }))}
          {wrapSlot((value.right as FormulaNode) || { op: "num", value: 0 }, (n) => onChange({ ...value, right: n, op }))}
        </Space>
      )}
      {op === "ref_builtin" && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">内置子线 </Typography.Text>
          <Select
            showSearch
            style={{ minWidth: 200 }}
            value={value.sub_name as string}
            options={(builtinSubNames.length ? builtinSubNames : ["MA5"]).map((s) => ({ value: s, label: s }))}
            onChange={(s) => onChange({ ...value, op: "ref_builtin", sub_name: s, fetch: value.fetch || { mode: "current" } })}
          />
          <FetchEditor
            value={(value.fetch as Record<string, unknown>) || { mode: "current" }}
            onChange={(f) => onChange({ ...value, op: "ref_builtin", sub_name: value.sub_name as string, fetch: f })}
            paramNames={paramNames.length ? paramNames : ["N"]}
            formulaSlot={wrapSlot}
          />
        </div>
      )}
      {op === "ref_sibling" && (
        <div style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">子线 key </Typography.Text>
          <Select
            style={{ minWidth: 160 }}
            value={value.sub_key as string}
            options={siblingChoices.map((s) => ({ value: s, label: s }))}
            onChange={(s) => onChange({ ...value, op: "ref_sibling", sub_key: s, fetch: value.fetch || { mode: "current" } })}
          />
          <FetchEditor
            value={(value.fetch as Record<string, unknown>) || { mode: "current" }}
            onChange={(f) => onChange({ ...value, op: "ref_sibling", sub_key: value.sub_key as string, fetch: f })}
            paramNames={paramNames.length ? paramNames : ["N"]}
            formulaSlot={wrapSlot}
          />
        </div>
      )}
    </Card>
  );
}

type BuilderProps = {
  value: UserIndicatorDefinitionDraft;
  onChange: (v: UserIndicatorDefinitionDraft) => void;
  /** 来自 GET .../builtin-catalog 打平后的子线名 */
  builtinSubNames: string[];
};

export function UserIndicatorBuilder({ value, onChange, builtinSubNames }: BuilderProps) {
  const paramNames = useMemo(() => value.params.map((p) => p.name).filter(Boolean), [value.params]);
  const subKeys = useMemo(() => value.sub_indicators.map((s) => s.key).filter(Boolean), [value.sub_indicators]);

  const updateParam = (i: number, patch: Partial<(typeof value.params)[0]>) => {
    const params = value.params.map((p, j) => (j === i ? { ...p, ...patch } : p));
    onChange({ ...value, params });
  };
  const addParam = () => {
    onChange({
      ...value,
      params: [...value.params, { name: `P${value.params.length + 1}`, description: "", default_value: "1" }],
    });
  };
  const removeParam = (i: number) => {
    onChange({ ...value, params: value.params.filter((_, j) => j !== i) });
  };

  const updateSub = (i: number, patch: Partial<SubIndicatorDraft>) => {
    const sub_indicators = value.sub_indicators.map((s, j) => (j === i ? { ...s, ...patch } : s));
    onChange({ ...value, sub_indicators });
  };
  const addSub = () => {
    const k = `line${value.sub_indicators.length + 1}`;
    onChange({
      ...value,
      sub_indicators: [
        ...value.sub_indicators,
        {
          key: k,
          name: "新子线",
          description: "",
          auxiliary_only: false,
          use_in_screening: true,
          use_in_chart: true,
          chart_kind: "line",
          initial_value: null,
          formula: { op: "intrinsic", field: "close" },
        },
      ],
    });
  };
  const removeSub = (i: number) => {
    if (value.sub_indicators.length <= 1) return;
    onChange({ ...value, sub_indicators: value.sub_indicators.filter((_, j) => j !== i) });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small" title="参数（默认值）">
        {value.params.map((p, i) => (
          <Space key={i} wrap style={{ marginBottom: 8 }} align="start">
            <Input placeholder="参数名" value={p.name} onChange={(e) => updateParam(i, { name: e.target.value })} style={{ width: 100 }} />
            <Input placeholder="说明" value={p.description} onChange={(e) => updateParam(i, { description: e.target.value })} style={{ width: 160 }} />
            <Input placeholder="默认值" value={p.default_value} onChange={(e) => updateParam(i, { default_value: e.target.value })} style={{ width: 100 }} />
            <Button type="text" danger icon={<DeleteOutlined />} onClick={() => removeParam(i)} />
          </Space>
        ))}
        <Button type="dashed" icon={<PlusOutlined />} onClick={addParam} block>
          添加参数
        </Button>
      </Card>
      <Card size="small" title="适用周期">
        <Select
          mode="multiple"
          style={{ width: "100%" }}
          value={value.periods}
          options={PERIOD_OPTS}
          onChange={(periods) => onChange({ ...value, periods: periods.length ? periods : ["1d"] })}
        />
      </Card>
      {value.sub_indicators.map((s, i) => (
        <Card
          key={s.key + i}
          size="small"
          title={`子线：${s.name}（key=${s.key}）`}
          extra={
            <Button type="link" danger size="small" onClick={() => removeSub(i)} disabled={value.sub_indicators.length <= 1}>
              删除
            </Button>
          }
        >
          <Space wrap style={{ marginBottom: 8 }}>
            <Input
              placeholder="key（小写）"
              value={s.key}
              onChange={(e) => updateSub(i, { key: e.target.value.trim().toLowerCase() })}
              style={{ width: 120 }}
            />
            <Input placeholder="名称" value={s.name} onChange={(e) => updateSub(i, { name: e.target.value })} style={{ width: 140 }} />
            <Input placeholder="简介" value={s.description} onChange={(e) => updateSub(i, { description: e.target.value })} style={{ width: 200 }} />
          </Space>
          <Space wrap style={{ marginBottom: 8 }}>
            <Typography.Text type="secondary">仅辅助</Typography.Text>
            <Switch
              checked={s.auxiliary_only}
              onChange={(c) =>
                updateSub(i, { auxiliary_only: c, ...(c ? { use_in_screening: false, use_in_chart: false, chart_kind: null } : {}) })
              }
            />
            <Typography.Text type="secondary">选股/回测</Typography.Text>
            <Switch checked={s.use_in_screening} disabled={s.auxiliary_only} onChange={(c) => updateSub(i, { use_in_screening: c })} />
            <Typography.Text type="secondary">图形</Typography.Text>
            <Switch checked={s.use_in_chart} disabled={s.auxiliary_only} onChange={(c) => updateSub(i, { use_in_chart: c })} />
            <Select
              placeholder="图形类型"
              disabled={s.auxiliary_only || !s.use_in_chart}
              style={{ width: 120 }}
              value={s.chart_kind || undefined}
              options={[
                { value: "line", label: "折线" },
                { value: "bar", label: "柱状" },
              ]}
              onChange={(c) => updateSub(i, { chart_kind: c })}
            />
          </Space>
          <Typography.Text type="secondary">初始值（可空）</Typography.Text>
          <Input
            style={{ maxWidth: 200, marginBottom: 8 }}
            value={s.initial_value ?? ""}
            onChange={(e) => updateSub(i, { initial_value: e.target.value || null })}
          />
          <Typography.Text strong>公式</Typography.Text>
          <FormulaEditor
            value={s.formula}
            onChange={(f) => updateSub(i, { formula: f })}
            paramNames={paramNames}
            subKeys={subKeys}
            builtinSubNames={builtinSubNames}
            selfSubKey={s.key}
          />
        </Card>
      ))}
      <Button type="dashed" onClick={addSub} icon={<PlusOutlined />} block>
        添加子指标
      </Button>
    </Space>
  );
}
