import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Progress,
  Switch,
  Table,
  Tag,
  Typography,
  Spin,
  message,
} from "antd";
import type { RadioChangeEvent } from "antd";
import type { ColumnsType } from "antd/es/table";
import { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  type SyncJob,
  type SyncRun,
  type DataCenterRow,
  fetchDataCenter,
  fetchSyncJob,
  fetchSyncRuns,
  fetchSyncBySelection,
  fetchTushareTokenStatus,
  getApiErrorMessage,
  setTushareToken,
  triggerSyncRun,
  updateSyncJob,
} from "../api/client";

export default function SyncPage() {
  const [job, setJob] = useState<SyncJob | null>(null);
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [form] = Form.useForm<{ cron_expr: string; enabled: boolean }>();

  const [symbolsLoading, setSymbolsLoading] = useState(false);
  const [allSymbols, setAllSymbols] = useState<DataCenterRow[]>([]);
  const [tokenStatus, setTokenStatus] = useState<{
    hasRuntime: boolean;
    hasDb?: boolean;
    hasEnv: boolean;
    configured: boolean;
    stockListLastSyncDate?: string | null;
  } | null>(null);
  const [tushareTokenDraft, setTushareTokenDraft] = useState("");
  const [tokenSaving, setTokenSaving] = useState(false);

  const [selectionMode, setSelectionMode] = useState<"single" | "multi">("multi");
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fromListing, setFromListing] = useState(false);

  const cronExpr = Form.useWatch("cron_expr", form);

  const cronDesc = useMemo(() => {
    const raw = (cronExpr || "").trim();
    const parts = raw.split(/\s+/);
    if (parts.length !== 5) return "请输入 5 段格式：分 时 日 月 周";
    const [m, h, d, mo, w] = parts;
    if (d === "*" && mo === "*" && w === "*" && /^\d+$/.test(m) && /^\d+$/.test(h)) {
      return `每天 ${h.padStart(2, "0")}:${m.padStart(2, "0")} 执行`;
    }
    if (d === "*" && mo === "*" && /^\d+$/.test(w) && /^\d+$/.test(m) && /^\d+$/.test(h)) {
      const weekMap: Record<string, string> = {
        "0": "周日",
        "1": "周一",
        "2": "周二",
        "3": "周三",
        "4": "周四",
        "5": "周五",
        "6": "周六",
        "7": "周日",
      };
      const wd = weekMap[w] ?? `周${w}`;
      return `每周 ${wd} ${h.padStart(2, "0")}:${m.padStart(2, "0")} 执行`;
    }
    return "已设置自定义 cron 表达式";
  }, [cronExpr]);

  const loadSymbols = useCallback(async () => {
    setSymbolsLoading(true);
    try {
      // 手动拉取的可选范围与“数据后台”严格统一。
      const list = await fetchDataCenter(2000);
      setAllSymbols(list);
      if (!list.length) {
        message.info("当前本地元数据为空，请先点击“同步全量标的元数据”");
      }
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setSymbolsLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const j = await fetchSyncJob();
      setJob(j);
      form.setFieldsValue({
        cron_expr: j.cron_expr,
        enabled: j.enabled,
      });
      const r = await fetchSyncRuns(30);
      setRuns(r);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [form]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === "queued" || r.status === "running");
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [runs, refresh]);

  useEffect(() => {
    // 进入页面先查 token 是否已就绪，就绪才拉全 A
    void (async () => {
      try {
        const st = await fetchTushareTokenStatus();
        setTokenStatus(st);
        if (st.configured) {
          await loadSymbols();
        }
      } catch (e) {
        message.error(getApiErrorMessage(e));
      }
    })();
  }, [loadSymbols]);

  const onSave = async () => {
    const v = await form.validateFields();
    try {
      const j = await updateSyncJob({
        cron_expr: v.cron_expr.trim(),
        enabled: v.enabled,
      });
      setJob(j);
      message.success("已保存");
      void refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    }
  };

  const onRunNow = async () => {
    setRunning(true);
    try {
      await triggerSyncRun();
      message.success("同步已触发");
      void refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunning(false);
    }
  };

  const symbolOptions = useMemo(() => {
    return allSymbols.map((s) => ({
      value: s.ts_code,
      label: s.name ? `${s.name} (${s.ts_code}) [${s.asset_type}]` : `${s.ts_code} [${s.asset_type}]`,
    }));
  }, [allSymbols]);

  const onChangeMode = (e: RadioChangeEvent) => {
    const v = e.target.value as "single" | "multi";
    setSelectionMode(v);
    if (v === "single") setSelectedCodes((prev) => (prev[0] ? [prev[0]] : []));
  };

  const onChangeCodes = (value: string | string[]) => {
    if (Array.isArray(value)) setSelectedCodes(value);
    else setSelectedCodes(value ? [value] : []);
  };

  const onFetchSelected = async () => {
    if (!tokenStatus?.configured) {
      message.error("请先在上方输入并校验 Tushare token");
      return;
    }
    if (selectedCodes.length === 0) {
      message.error("请先选择至少一个标的");
      return;
    }
    if (!fromListing && (!range || range.length !== 2)) {
      message.error("请选择日期范围");
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    const start_date = range?.[0]?.format("YYYY-MM-DD") ?? today;
    const end_date = range?.[1]?.format("YYYY-MM-DD") ?? today;
    if (!fromListing && start_date > end_date) {
      message.error("日期范围不合法：开始日不能大于结束日");
      return;
    }

    setFetching(true);
    try {
      await fetchSyncBySelection({
        ts_codes: selectedCodes,
        start_date: fromListing ? undefined : start_date,
        end_date,
        from_listing: fromListing,
      });
      message.success("拉取已触发（请在下方查看运行进度）");
      void refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setFetching(false);
    }
  };

  const onSetToken = async () => {
    const token = tushareTokenDraft.trim();
    if (!token) {
      message.error("请输入 token");
      return;
    }
    setTokenSaving(true);
    try {
      await setTushareToken(token);
      message.success("token 已保存，同步任务执行前会自动校验");
      setTushareTokenDraft("");
      const st = await fetchTushareTokenStatus();
      setTokenStatus(st);
      await loadSymbols();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setTokenSaving(false);
    }
  };

  const columns: ColumnsType<SyncRun> = [
    { title: "ID", dataIndex: "id", width: 64 },
    { title: "开始", dataIndex: "started_at", width: 180 },
    { title: "结束", dataIndex: "finished_at", width: 180 },
    { title: "触发", dataIndex: "trigger", width: 100 },
    { title: "状态", dataIndex: "status", width: 100 },
    {
      title: "进度/摘要",
      dataIndex: "message",
      render: (_: string | null, record) => {
        const msg = record.message || "-";
        const mProg = msg.match(/progress\s+(\d+)\/(\d+)/i);
        const adjFail = Number(msg.match(/adj_fail=(\d+)/i)?.[1] ?? 0);
        if (!mProg) return msg;
        const cur = Number(mProg[1] || 0);
        const total = Number(mProg[2] || 0);
        const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((cur / total) * 100))) : 0;
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Space size={4}>
              <Progress percent={percent} size="small" showInfo={false} style={{ width: 120 }} />
              {adjFail > 0 && (
                <Tag color="orange" title="部分标的复权因子拉取失败，查看日志中 [ADJ_FAIL] 条目">
                  复权因子失败 {adjFail}
                </Tag>
              )}
            </Space>
            <Typography.Text type="secondary">
              {msg}
              {msg.includes("eta=") ? "" : "（ETA 计算中）"}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "日志文件",
      dataIndex: "log_path",
      ellipsis: true,
      render: (_: string | null, record) =>
        record.log_path ? (
          <a href={`/api/sync/runs/${record.id}/log`} target="_blank" rel="noreferrer">
            打开日志
          </a>
        ) : (
          "-"
        ),
    },
  ];


  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        同步任务
      </Typography.Title>
      {job?.last_error ? (
        <Alert type="error" message="上次错误" description={job.last_error} />
      ) : null}
      <Card title="手动拉取数据（数据后台范围 + 选标的 + 时间范围）">
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type={tokenStatus?.configured ? "success" : "warning"}
            showIcon
            message={
              tokenStatus?.configured ? "Tushare token 已就绪" : "未配置 token：请先输入并校验后再拉取"
            }
            description={
              tokenStatus?.configured
                ? `来源：${
                    tokenStatus.hasRuntime
                      ? "运行时"
                      : tokenStatus.hasDb
                        ? "后台持久化"
                        : tokenStatus.hasEnv
                          ? ".env"
                          : "未知"
                  }；股票列表最近刷新：${tokenStatus.stockListLastSyncDate || "暂无"}`
                : undefined
            }
          />
          <Space style={{ width: "100%" }} wrap>
            <Input.Password
              style={{ flex: 1, minWidth: 320 }}
              placeholder="输入 TUSHARE_TOKEN"
              value={tushareTokenDraft}
              onChange={(e) => setTushareTokenDraft(e.target.value)}
            />
            <Button type="primary" loading={tokenSaving} onClick={() => void onSetToken()}>
              保存 token
            </Button>
          </Space>

          <Space wrap>
            <Radio.Group value={selectionMode} onChange={onChangeMode}>
              <Radio value="multi">多选</Radio>
              <Radio value="single">单选</Radio>
            </Radio.Group>
            <Spin spinning={symbolsLoading} />
          </Space>

          <Select
            mode={selectionMode === "multi" ? "multiple" : undefined}
            showSearch
            allowClear
            style={{ minWidth: 520, width: "100%" }}
            placeholder="搜索股票（名称/代码）"
            value={selectionMode === "multi" ? selectedCodes : selectedCodes[0] || undefined}
            options={symbolOptions}
            onChange={onChangeCodes}
            filterOption={(input, option) => {
              const label = typeof option?.label === "string" ? option.label : "";
              const value = (option?.value as string) || "";
              const q = input.trim().toLowerCase();
              if (!q) return true;
              return label.toLowerCase().includes(q) || value.toLowerCase().includes(q);
            }}
          />

          <DatePicker.RangePicker
            value={range}
            style={{ width: 520 }}
            disabled={fromListing}
            onChange={(v) => {
              if (!v) setRange(null);
              else setRange([v[0] as Dayjs, v[1] as Dayjs]);
            }}
          />
          <Space>
            <Switch checked={fromListing} onChange={setFromListing} />
            <Typography.Text>从上市以来拉取（忽略日期选择，默认拉到今天）</Typography.Text>
          </Space>

          <Space>
            <Button
              type="primary"
              loading={fetching}
              onClick={() => void onFetchSelected()}
              disabled={!tokenStatus?.configured}
            >
              立即拉取（按所选股票）
            </Button>
            <Button onClick={() => setSelectedCodes([])} disabled={!selectedCodes.length}>
              清空选择
            </Button>
          </Space>
        </Space>
      </Card>
      <Card title="定时配置" loading={loading}>
        <Form
          form={form}
          layout="vertical"
          style={{ maxWidth: 480 }}
          initialValues={{ cron_expr: "0 18 * * *", enabled: true }}
        >
          <Form.Item
            label="Cron（5 段：分 时 日 月 周）"
            name="cron_expr"
            rules={[{ required: true, message: "请输入 cron" }]}
            extra={cronDesc}
          >
            <Input placeholder="例如 0 18 * * * 每天 18:00" />
          </Form.Item>
          <Space wrap style={{ marginBottom: 12 }}>
            <Button size="small" onClick={() => form.setFieldValue("cron_expr", "0 18 * * *")}>
              每天 18:00
            </Button>
            <Button size="small" onClick={() => form.setFieldValue("cron_expr", "0 9 * * 1")}>
              每周一 09:00
            </Button>
            <Button size="small" onClick={() => form.setFieldValue("cron_expr", "0 21 * * 1-5")}>
              工作日 21:00
            </Button>
          </Space>
          <Form.Item label="启用" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space>
            <Button type="primary" onClick={() => void onSave()}>
              保存
            </Button>
            <Button loading={running} onClick={() => void onRunNow()}>
              立即执行
            </Button>
            <Button onClick={() => void refresh()}>刷新</Button>
          </Space>
        </Form>
      </Card>
      <Card title="最近运行">
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={runs}
          pagination={{ pageSize: 10 }}
        />
      </Card>
    </Space>
  );
}
