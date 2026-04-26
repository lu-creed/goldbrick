/**
 * 数据同步页面
 *
 * 功能：管理从 Tushare 拉取股票数据的所有操作，包括：
 * - 手动拉取（按所选股票、全市场、全指数）
 * - 定时任务配置（设定 cron 表达式，每天自动同步）
 * - 同步日志（查看历史运行记录、实时进度、暂停/继续/取消）
 *
 * 关键概念：
 * - Tushare：提供 A 股数据的 API 平台，需要 token（积分 >= 320 才可用日线接口）
 * - SyncJob：定时任务配置（唯一一条）
 * - SyncRun：每次实际运行的记录（一次可能包含数千只股票）
 * - 协作式取消：不会立即中断，而是在当前股票处理完后再停止
 */
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Popconfirm,
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
import { useLocation } from "react-router-dom";
import {
  type SyncJob,
  type SyncRun,
  type SymbolRow,
  fetchSymbols,
  cancelSyncRun,
  fetchSyncJob,
  fetchSyncRuns,
  fetchSyncAllIndexPool,
  fetchSyncAllMarket,
  fetchSyncBySelection,
  fetchTushareTokenStatus,
  pauseSyncRun,
  getApiErrorMessage,
  setTushareToken,
  triggerSyncRun,
  resumeSyncRun,
  updateSyncJob,
} from "../api/client";
import { zebraRowClass } from "../constants/theme";

export default function SyncPage() {
  const location = useLocation();
  // job：定时任务配置（cron 表达式、是否启用等）
  const [job, setJob] = useState<SyncJob | null>(null);
  // runs：最近 30 条同步运行记录
  const [runs, setRuns] = useState<SyncRun[]>([]);
  // loading：是否正在加载定时任务配置（控制卡片加载状态）
  const [loading, setLoading] = useState(false);
  // running：是否正在触发立即执行（防止重复点击）
  const [running, setRunning] = useState(false);
  const [form] = Form.useForm<{ cron_expr: string; enabled: boolean }>();

  // symbolsLoading：是否正在加载股票列表
  const [symbolsLoading, setSymbolsLoading] = useState(false);
  // allSymbols：本地元数据中的全部股票/指数（用于搜索下拉选择）
  const [allSymbols, setAllSymbols] = useState<SymbolRow[]>([]);
  // tokenStatus：Tushare token 的配置状态
  const [tokenStatus, setTokenStatus] = useState<{
    hasRuntime: boolean;
    hasDb?: boolean;
    hasEnv: boolean;
    configured: boolean;
    stockListLastSyncDate?: string | null;
  } | null>(null);
  const [tushareTokenDraft, setTushareTokenDraft] = useState("");
  const [tokenSaving, setTokenSaving] = useState(false);

  // selectionMode：手动拉取时的选股模式（单选 or 多选）
  const [selectionMode, setSelectionMode] = useState<"single" | "multi">("multi");
  // selectedCodes：已选中的股票/指数代码列表
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  // range：手动拉取的日期范围
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  // fetching / fetchingAll / fetchingAllIndex：各个拉取按钮的 loading 状态
  const [fetching, setFetching] = useState(false);
  const [fetchingAll, setFetchingAll] = useState(false);
  const [fetchingAllIndex, setFetchingAllIndex] = useState(false);
  // runActionId：当前正在操作（暂停/继续/取消）的运行 ID（防止连点）
  const [runActionId, setRunActionId] = useState<number | null>(null);

  /** 顶栏「同步日志」入口带 #sync-runs 时滚动到运行记录表（对齐 PRD：同步日志） */
  useEffect(() => {
    if (location.pathname === "/sync" && location.hash === "#sync-runs") {
      document.getElementById("sync-runs")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [location.hash, location.pathname]);
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
      const list = await fetchSymbols();
      setAllSymbols(list);
      if (!list.length) {
        message.info("当前本地标的列表为空，请先点击“同步全量标的元数据”");
      }
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setSymbolsLoading(false);
    }
  }, []);

  /**
   * 拉取定时任务与运行记录。
   * silent: true 时不触发 Card 的 loading（用于同步进行中的轮询），避免「定时配置」页签每 2.5s 闪一次整卡骨架导致页面抖动。
   */
  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    if (!silent) setLoading(true);
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
      if (!silent) setLoading(false);
    }
  }, [form]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const hasRunning = runs.some((r) => ["queued", "running", "paused"].includes(r.status));
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      void refresh({ silent: true });
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
      label: s.name ? `${s.name} (${s.ts_code})` : s.ts_code,
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

  /** 与「按所选股票」相同的日期规则，标的由后端按元数据全部个股取齐，避免前端全选/超大请求体 */
  const onFetchAllMarket = async () => {
    if (!tokenStatus?.configured) {
      message.error("请先在上方输入并校验 Tushare token");
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

    setFetchingAll(true);
    try {
      await fetchSyncAllMarket({
        start_date: fromListing ? undefined : start_date,
        end_date,
        from_listing: fromListing,
      });
      message.success("全市场拉取已触发（请在下方查看运行进度）");
      void refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setFetchingAll(false);
    }
  };

  /** 与全市场个股相同日期规则；标的为数据后台「指数」页签已登记的全部指数 */
  const onFetchAllIndexPool = async () => {
    if (!tokenStatus?.configured) {
      message.error("请先在上方输入并校验 Tushare token");
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

    setFetchingAllIndex(true);
    try {
      await fetchSyncAllIndexPool({
        start_date: fromListing ? undefined : start_date,
        end_date,
        from_listing: fromListing,
      });
      message.success("全市场指数拉取已触发（请在下方查看运行进度）");
      void refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setFetchingAllIndex(false);
    }
  };

  const onPauseRun = async (id: number) => {
    setRunActionId(id);
    try {
      await pauseSyncRun(id);
      message.success("已请求暂停（当前标的完成后生效）");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
    }
  };

  const onResumeRun = async (id: number) => {
    setRunActionId(id);
    try {
      await resumeSyncRun(id);
      message.success("已继续");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
    }
  };

  const onCancelRun = async (id: number) => {
    setRunActionId(id);
    try {
      await cancelSyncRun(id);
      message.success("已请求取消（当前标的完成后停止）");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
    }
  };

  /** 库内直接收口 cancelled：解决后台线程已退出或卡死导致 ordinary 取消无效、长期 running */
  const onForceCancelRun = async (id: number) => {
    setRunActionId(id);
    try {
      await cancelSyncRun(id, { force: true });
      message.success("已强制记为已取消；请刷新运行列表");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
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
        const hideEta = record.status === "paused" || msg.includes("已暂停");
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
              {hideEta || msg.includes("eta=") ? "" : "（ETA 计算中）"}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 300,
      render: (_: unknown, record: SyncRun) => {
        const active = ["queued", "running", "paused"].includes(record.status);
        if (!active) return "—";
        const loading = runActionId === record.id;
        const showPause =
          (record.status === "queued" || record.status === "running") && !record.pause_requested;
        const showResume = Boolean(record.pause_requested) || record.status === "paused";
        return (
          <Space size="small" wrap>
            {showPause ? (
              <Button size="small" loading={loading} onClick={() => void onPauseRun(record.id)}>
                暂停
              </Button>
            ) : null}
            {showResume ? (
              <Button size="small" loading={loading} onClick={() => void onResumeRun(record.id)}>
                继续
              </Button>
            ) : null}
            <Popconfirm title="确定取消本次同步？" onConfirm={() => void onCancelRun(record.id)}>
              <Button size="small" danger loading={loading}>
                取消
              </Button>
            </Popconfirm>
            <Popconfirm
              title="强制结束本条运行？"
              description="仅当多次点「取消」仍显示 running 时用：直接把状态改为已取消。若仍有工作线程，其会尽快退出。"
              onConfirm={() => void onForceCancelRun(record.id)}
            >
              <Button size="small" danger type="dashed" loading={loading}>
                强制结束
              </Button>
            </Popconfirm>
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
        数据同步
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

          <Space wrap>
            <Button
              type="primary"
              loading={fetching}
              onClick={() => void onFetchSelected()}
              disabled={!tokenStatus?.configured || fetchingAll || fetchingAllIndex}
            >
              立即拉取（按所选股票）
            </Button>
            <Popconfirm
              title="全市场拉取"
              description="将按数据池中的全部个股（元数据股票列表）按上方日期规则拉取，耗时长、调用配额高，确定继续？"
              okText="确定"
              cancelText="取消"
              disabled={!tokenStatus?.configured}
              onConfirm={() => void onFetchAllMarket()}
            >
              <Button
                loading={fetchingAll}
                disabled={!tokenStatus?.configured || fetching || fetchingAllIndex}
              >
                全市场拉取（无需选代码）
              </Button>
            </Popconfirm>
            <Popconfirm
              title="全市场指数拉取"
              description="将按数据后台已登记的全部指数按上方日期规则拉取（index_daily，无复权）；请先在同一后台完成指数登记。"
              okText="确定"
              cancelText="取消"
              disabled={!tokenStatus?.configured}
              onConfirm={() => void onFetchAllIndexPool()}
            >
              <Button
                loading={fetchingAllIndex}
                disabled={!tokenStatus?.configured || fetching || fetchingAll}
              >
                全市场指数拉取
              </Button>
            </Popconfirm>
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
      <Card id="sync-runs" title="最近运行">
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="协作式取消：在上一只标的整段处理（含单次 Tushare 请求）结束后才会停；该段期间仍为 running。若多次「取消」仍不变，请用「强制结束」在库中直接收口（多为后台线程已丢或进程重启）。日志在任务开始写入后即可打开。"
        />
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={runs}
          rowClassName={zebraRowClass}
          pagination={{ pageSize: 10 }}
        />
      </Card>
    </Space>
  );
}
