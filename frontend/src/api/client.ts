/**
 * API 客户端：前端与后端通信的唯一出口
 *
 * 下面每个函数对应后端一条接口（路径都在 /api 下）。
 * 页面里只调用这里，不要到处手写网址，以后改路径只改这一文件。
 *
 * 与页面的对应关系（按需搜函数名即可）：
 * - K 线页：fetchBars、fetchSymbols
 * - 同步页：fetchSyncJob、triggerSyncRun、fetchSyncBySelection、pauseSyncRun、resumeSyncRun、cancelSyncRun、token…
 * - 数据池：fetchDataCenter、syncStockListMeta、fetchIndexCandidates、applyIndexMetaSelection…
 * - 指标库：fetchIndicators、fetchIndicatorDetail、seedIndicators；自定义指标 custom* 系列
 * - 个股列表：fetchDailyUniverse
 * - 股票复盘：fetchReplayDaily
 * - 条件选股：runScreening；历史记录：fetchScreeningHistory、fetchScreeningHistoryDetail、deleteScreeningHistory
 * - 回测：runBacktest；历史记录：fetchBacktestRecords、fetchBacktestRecordDetail、deleteBacktestRecord
 * - 自选股池：fetchWatchlist、addToWatchlist、removeFromWatchlist
 */
import axios from "axios";

/**
 * Axios 实例：统一配置后端地址和超时时间
 * - baseURL: "/api" 表示所有请求都发到同一台机器（由 Vite 代理到 FastAPI）
 * - timeout: 120s，因为部分接口（如全市场扫描）耗时较长
 */
export const api = axios.create({
  baseURL: "/api",
  timeout: 120_000,
});

// ── Auth 拦截器 ────────────────────────────────────────────────

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("gb_token");
  if (token) {
    config.headers = config.headers ?? {};
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (axios.isAxiosError(err) && err.response?.status === 401) {
      localStorage.removeItem("gb_token");
      localStorage.removeItem("gb_user");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  },
);

/**
 * 从 Axios 错误对象中提取可读的错误信息
 * 后端返回的错误会放在 response.data.detail 里（FastAPI 规范）
 *
 * @param error - 任意类型的错误对象
 * @returns 人类可读的错误字符串（用于 message.error 显示）
 */
export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    const msg = error.message;
    if (msg && msg.trim()) {
      return msg;
    }
  }
  return error instanceof Error ? error.message : String(error);
}

// ── 类型定义 ───────────────────────────────────────────────────

/** 股票/指数基本信息（用于下拉选择列表） */
export type SymbolRow = {
  id: number;
  ts_code: string;   // Tushare 格式代码，如 "000001.SZ"
  name: string | null;
};

/**
 * 一根 K 线蜡烛的数据
 * 包含 OHLCV（开高低收量额）和衍生统计字段
 */
export type BarPoint = {
  time: string;               // 交易日，格式 "YYYY-MM-DD"
  open: number;               // 开盘价
  high: number;               // 最高价
  low: number;                // 最低价
  close: number;              // 收盘价
  volume: number;             // 成交量（手）
  amount: number;             // 成交额（元）
  turnover_rate_avg: number | null;          // 日均换手率（周/月/季/年 K 时取均值）
  consecutive_limit_up_days: number | null;  // 连续涨停天数
  consecutive_limit_down_days: number | null; // 连续跌停天数
  consecutive_up_days: number | null;        // 连续上涨天数
  consecutive_down_days: number | null;      // 连续下跌天数
};

/** K 线周期：1d=日K，1w=周K，1M=月K，1Q=季K，1y=年K */
export type Interval = "1d" | "1w" | "1M" | "1Q" | "1y";

/** 定时同步任务的配置（唯一一条，用 cron 表达式控制执行时间） */
export type SyncJob = {
  id: number;
  cron_expr: string;        // cron 表达式，如 "0 18 * * *"（每天 18:00）
  enabled: boolean;         // 是否启用定时任务
  last_run_at: string | null;   // 上次执行时间
  last_status: string | null;   // 上次执行状态（success/error）
  last_error: string | null;    // 上次执行错误信息
};

/** 一次实际同步运行的记录 */
export type SyncRun = {
  id: number;
  started_at: string;           // 开始时间
  finished_at: string | null;   // 结束时间（运行中时为 null）
  trigger: string;              // 触发方式（scheduler=定时，manual=手动）
  status: string;               // 状态：queued/running/paused/done/cancelled/error
  message: string | null;       // 进度信息（如 "progress 100/5000"）
  log_path: string | null;      // 日志文件路径（可通过接口下载）
  pause_requested?: boolean;    // 用户已请求暂停（在下一只标的处生效）
  cancel_requested?: boolean;   // 用户已请求取消
};

// ── 股票列表 ─────────────────────────────────────────────────

/** 获取本地元数据中的全部股票/指数列表（用于各页面的搜索下拉） */
export async function fetchSymbols() {
  const { data } = await api.get<SymbolRow[]>("/symbols");
  return data;
}

/** 复权方式：none=不复权，qfq=前复权，hfq=后复权 */
export type AdjType = "none" | "qfq" | "hfq";

// ── K 线数据 ─────────────────────────────────────────────────

/**
 * 获取某只股票/指数的 K 线数据
 * @param params.ts_code  - 股票代码（如 "000001.SZ"）
 * @param params.interval - K 线周期（1d/1w/1M/1Q/1y）
 * @param params.start    - 起始日期（可选，格式 "YYYY-MM-DD"）
 * @param params.end      - 结束日期（可选）
 * @param params.adj      - 复权方式（none/qfq/hfq）
 * @returns 按时间升序排列的 K 线数组
 */
export async function fetchBars(params: {
  ts_code: string;
  interval: Interval;
  start?: string;
  end?: string;
  adj?: AdjType;
}) {
  const { data } = await api.get<BarPoint[]>("/bars", { params });
  return data;
}

/** 自定义指标时间序列的返回结构 */
export type CustomIndicatorSeriesOut = {
  ts_code: string;
  user_indicator_id: number;
  sub_key: string;               // 子线 key（一个指标可有多条输出线）
  display_name: string;          // 指标显示名（用于图例标题）
  points: { time: string; value: number | null }[];  // 数据点列表
};

/**
 * 获取某只股票的自定义指标时间序列（用于 K 线副图）
 * @param params.ts_code             - 股票代码
 * @param params.user_indicator_id   - 自定义指标 ID
 * @param params.sub_key             - 要展示的子线 key
 * @param params.adj                 - 复权方式（应与 K 线一致）
 */
export async function fetchCustomIndicatorSeries(params: {
  ts_code: string;
  user_indicator_id: number;
  sub_key: string;
  adj?: AdjType;
  start?: string;
  end?: string;
}) {
  const { data } = await api.get<CustomIndicatorSeriesOut>("/bars/custom-indicator-series", { params });
  return data;
}

// ── 条件选股 ─────────────────────────────────────────────────

/** 选股结果中的单只股票行 */
export type ScreeningStockRow = {
  ts_code: string;
  name: string | null;
  close: number;              // 收盘价
  pct_change: number | null;  // 涨跌幅
  indicator_value: number;    // 该股该日的指标值
};

/** 一次选股运行的完整返回结果 */
export type ScreeningRunOut = {
  trade_date: string;           // 实际使用的交易日
  user_indicator_id: number | null;
  sub_key: string | null;       // 参与选股的子线
  compare_op: string | null;    // 比较运算符（gt/gte/lt/le/eq/ne）
  threshold: number | null;     // 比较阈值
  scanned: number;              // 实际扫描的股票数
  matched: number;              // 满足条件的股票数
  note: string | null;          // 后端警告信息（如数据不完整）
  items: ScreeningStockRow[];   // 命中的股票列表
  history_id: number | null;    // 本次选股自动保存的历史记录 ID（保存失败时为 null）
};

/**
 * 执行一次条件选股扫描
 * @param body.trade_date        - 扫描的交易日
 * @param body.user_indicator_id - 用哪个自定义指标
 * @param body.sub_key           - 用哪条子线（旧版指标不传）
 * @param body.compare_op        - 比较运算符（默认 gt）
 * @param body.threshold         - 阈值
 * @param body.max_scan          - 最多扫描数（防超时，默认 6000）
 */
export async function runScreening(body: {
  trade_date: string;
  user_indicator_id: number;
  sub_key?: string;
  compare_op?: string;
  threshold?: number;
  max_scan?: number;
}) {
  const { data } = await api.post<ScreeningRunOut>("/screening/run", body);
  return data;
}

// ── 条件选股历史记录 ─────────────────────────────────────────────

/** 选股历史记录的列表摘要（不含命中股票列表） */
export type ScreeningHistoryItem = {
  id: number;
  created_at: string;             // 执行时间（ISO 格式）
  trade_date: string;             // 选股日期
  indicator_name: string;         // 指标显示名（冗余存储，删除指标后仍可展示）
  indicator_code: string;         // 指标英文标识
  user_indicator_id: number | null;
  sub_key: string | null;         // 参与选股的子线 key
  compare_op: string | null;      // 比较运算符（gt/gte/lt/le/eq/ne）
  threshold: number;              // 阈值
  scanned: number;                // 扫描总数
  matched: number;                // 命中数
};

/** 选股历史详情（包含命中的股票列表，从 result_json 反序列化） */
export type ScreeningHistoryDetail = ScreeningHistoryItem & {
  items: ScreeningStockRow[];     // 命中的股票列表
};

/**
 * 获取条件选股历史记录列表（按执行时间倒序）
 * @param params.page      - 页码（从 1 开始）
 * @param params.page_size - 每页条数（5~100）
 */
export async function fetchScreeningHistory(params?: { page?: number; page_size?: number }) {
  const { data } = await api.get<ScreeningHistoryItem[]>("/screening/history", { params });
  return data;
}

/** 获取单条选股历史的详情（含命中股票列表） */
export async function fetchScreeningHistoryDetail(id: number) {
  const { data } = await api.get<ScreeningHistoryDetail>(`/screening/history/${id}`);
  return data;
}

/** 删除指定的选股历史记录（不可恢复） */
export async function deleteScreeningHistory(id: number) {
  const { data } = await api.delete<{ ok: boolean }>(`/screening/history/${id}`);
  return data;
}

// ── 同步任务管理 ─────────────────────────────────────────────

/** 获取当前定时任务配置（全系统唯一一条） */
export async function fetchSyncJob() {
  const { data } = await api.get<SyncJob>("/sync/job");
  return data;
}

/**
 * 更新定时任务配置（cron 表达式、是否启用）
 * @param body.cron_expr - 5 段 cron 表达式，如 "0 18 * * *"
 * @param body.enabled   - 是否启用
 */
export async function updateSyncJob(body: {
  cron_expr?: string;
  enabled?: boolean;
}) {
  const { data } = await api.put<SyncJob>("/sync/job", body);
  return data;
}

/** 立即触发一次同步（等同于手动点"立即执行"） */
export async function triggerSyncRun() {
  const { data } = await api.post<SyncRun>("/sync/run");
  return data;
}

/**
 * 获取最近 N 条同步运行记录
 * @param limit - 最多返回多少条（默认 20）
 */
export async function fetchSyncRuns(limit = 20) {
  const { data } = await api.get<SyncRun[]>("/sync/runs", { params: { limit } });
  return data;
}

/** 请求暂停某次正在运行的同步（在当前标的处理完成后才会暂停） */
export async function pauseSyncRun(runId: number) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/pause`);
  return data;
}

/** 恢复某次已暂停的同步 */
export async function resumeSyncRun(runId: number) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/resume`);
  return data;
}

/**
 * 请求取消某次同步
 * @param opts.force - true 时直接把状态改为 cancelled（用于卡死或进程重启后的清理）
 */
export async function cancelSyncRun(runId: number, opts?: { force?: boolean }) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/cancel`, {}, {
    params: opts?.force ? { force: true } : {},
  });
  return data;
}

/** Tushare 股票代码和名称（内部使用，从 Tushare 直接拉取） */
export type TushareSymbol = {
  ts_code: string;
  name: string | null;
};

/** 从 Tushare 直接获取全量 A 股代码列表（不经过本地数据库） */
export async function fetchAllASymbols() {
  const { data } = await api.get<TushareSymbol[]>("/tushare/symbols");
  return data;
}

/**
 * 手动触发指定股票列表的数据拉取
 * @param body.ts_codes      - 要拉取的股票代码列表
 * @param body.start_date    - 起始日期（from_listing=true 时忽略）
 * @param body.end_date      - 结束日期
 * @param body.from_listing  - true 则从上市以来全量拉取
 */
export async function fetchSyncBySelection(body: {
  ts_codes: string[];
  start_date?: string;
  end_date: string;
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch", body);
  return data;
}

/** 触发全市场个股数据拉取（后端自动取元数据中全部个股，无需前端传代码） */
export async function fetchSyncAllMarket(body: {
  start_date?: string;
  end_date: string;
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch-all", body);
  return data;
}

/** 触发数据池中全部指数的数据拉取（走 index_daily 接口，无复权） */
export async function fetchSyncAllIndexPool(body: {
  start_date?: string;
  end_date: string;
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch-all-index", body);
  return data;
}

/** 股票列表元数据同步的返回结果（更新了多少只） */
export type UniverseSyncOut = {
  stock_count: number;    // 当前总个股数
  index_count: number;    // 当前总指数数
  total: number;
  from_cache: boolean;
  last_sync_date?: string | null;
  inserted_stocks?: number;   // 本次新增个股数
  updated_stocks?: number;    // 本次更新信息的个股数
};

/** 数据池中每行股票/指数的状态信息 */
export type DataCenterRow = {
  ts_code: string;
  name: string | null;
  asset_type: string;           // "stock" 或 "index"
  list_date: string | null;     // 上市日期
  market: string | null;        // 市场类别（如 "主板"）
  exchange: string | null;      // 交易所代码（SSE/SZSE/BSE）
  synced_once: boolean;         // 是否曾经做过全量同步
  first_bar_date: string | null; // 本地最早的 K 线日期
  last_bar_date: string | null;  // 本地最新的 K 线日期
  bar_count: number;            // 本地 K 线总条数
  adj_factor_count: number;     // 已同步的复权因子条数
  adj_factor_coverage_ratio: number; // 复权因子覆盖率（0~1）
  adj_factor_synced: boolean;   // 复权因子是否已完整同步
};

/** 从 Tushare 拉取最新股票列表元数据（新增/更新个股信息，不拉日线） */
export async function syncStockListMeta() {
  const { data } = await api.post<UniverseSyncOut>("/sync/stock-list");
  return data;
}

/** @deprecated 请用 syncStockListMeta；仍指向同一接口 */
export async function syncUniverseMeta(_force = false) {
  return syncStockListMeta();
}

/** Tushare 上的指数候选行（用于指数登记弹窗） */
export type IndexCandidateRow = {
  ts_code: string;
  name: string | null;
  market: string | null;      // 市场（SSE/SZSE/CSI 等）
  publisher: string | null;   // 发布方（如"中证指数公司"）
  list_date: string | null;
};

/**
 * 从 Tushare 获取可加入数据池的指数候选列表
 * @param params.market - 按市场过滤（可选）
 * @param params.limit  - 最多返回多少条
 */
export async function fetchIndexCandidates(params?: { market?: string; limit?: number }) {
  const { data } = await api.get<IndexCandidateRow[]>("/sync/index-candidates", { params });
  return data;
}

/** 把选中的指数写入本地元数据池（已存在的会跳过） */
export async function applyIndexMetaSelection(items: { ts_code: string; name?: string | null; list_date?: string | null }[]) {
  const { data } = await api.post<{ added: number; skipped: number }>("/sync/index-meta/apply", { items });
  return data;
}

/**
 * 获取数据池全表（包含每只股票/指数的同步状态）
 * @param limit - 最多返回多少条（默认 500，传 5000 可获取全量）
 */
export async function fetchDataCenter(limit = 500) {
  const { data } = await api.get<DataCenterRow[]>("/sync/data-center", { params: { limit } });
  return data;
}

/** 某只股票/指数某日的日线数据行 */
export type SymbolDailyRow = {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover_rate: number | null;
  has_adj_factor: boolean;  // 该日是否有复权因子（影响前/后复权计算）
};

/** 分页日线数据的返回结构 */
export type SymbolDailyPage = { total: number; items: SymbolDailyRow[] };

/**
 * 获取某只股票/指数的历史日线数据（分页）
 * @param params.ts_code   - 股票代码
 * @param params.start     - 起始日期（可选）
 * @param params.end       - 结束日期（可选）
 * @param params.page      - 页码（从 1 开始）
 * @param params.page_size - 每页条数
 */
export async function fetchSymbolDaily(params: {
  ts_code: string;
  start?: string;
  end?: string;
  page?: number;
  page_size?: number;
}) {
  const { ts_code, ...rest } = params;
  const { data } = await api.get<SymbolDailyPage>(`/sync/symbol/${encodeURIComponent(ts_code)}/daily`, {
    params: rest,
  });
  return data;
}

/** 触发对某只股票某个交易日的单日补充同步 */
export async function triggerSingleDaySync(ts_code: string, trade_date: string) {
  const { data } = await api.post<SyncRun>("/sync/single-day", { ts_code, trade_date });
  return data;
}

/** Tushare token 的配置状态（来源优先级：运行时 > 数据库持久化 > .env） */
export type TushareTokenStatus = {
  hasRuntime: boolean;              // 是否通过 UI 设置过（本次运行时有效）
  hasDb?: boolean;                  // 是否持久化在数据库中
  hasEnv: boolean;                  // 是否配置在 .env 文件中
  configured: boolean;              // 综合判断：是否已配置（以上任一有效即为 true）
  stockListLastSyncDate?: string | null; // 股票列表最近刷新日期
};

// ── Tushare Token 管理 ─────────────────────────────────────────

/** 获取 Tushare token 的当前配置状态（不返回 token 明文） */
export async function fetchTushareTokenStatus() {
  const { data } = await api.get<TushareTokenStatus>("/admin/tushare/token-status");
  return data;
}

/** 保存 Tushare token（持久化到数据库，重启后仍有效） */
export async function setTushareToken(token: string) {
  const { data } = await api.post<{ ok: boolean }>("/admin/tushare/token", { token });
  return data;
}

// ── 内置指标库 ────────────────────────────────────────────────

/** 内置指标的列表项（用于指标库列表页） */
export type IndicatorListItem = {
  id: number;
  name: string;           // 英文标识，如 "MA"
  display_name: string;   // 中文名，如 "移动平均线"
  description: string | null;
  params_count: number;   // 参数数量
  sub_count: number;      // 子指标数量（MA 有多个周期输出）
};

/** 内置指标的详细信息（含参数列表和子指标列表） */
export type IndicatorDetail = {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  params: { id: number; name: string; description: string | null; default_value: string | null }[];
  sub_indicators: { id: number; name: string; description: string | null; can_be_price: boolean }[];
};

/** 获取所有内置指标列表 */
export async function fetchIndicators() {
  const { data } = await api.get<IndicatorListItem[]>("/indicators");
  return data;
}

/** 重新初始化/种入内置指标数据（一般只需执行一次） */
export async function seedIndicators(force = true) {
  const { data } = await api.post<{ message: string }>(`/indicators/seed?force=${force}`);
  return data;
}

/** 获取某个内置指标的详细信息（参数、子指标） */
export async function fetchIndicatorDetail(id: number) {
  const { data } = await api.get<IndicatorDetail>(`/indicators/${id}`);
  return data;
}

// ── 用户自定义指标 ──────────────────────────────────────────

/** 自定义指标类型：dsl=新版多子线，legacy=旧版单条表达式 */
export type UserIndicatorKind = "dsl" | "legacy";

/** 自定义指标的完整输出结构 */
export type UserIndicatorOut = {
  id: number;
  code: string;            // 英文标识（唯一），创建后不可修改
  display_name: string;    // 显示名称
  description: string | null;
  kind: UserIndicatorKind;
  definition: Record<string, unknown> | null;  // DSL 指标的完整定义（JSON）
  expr: string | null;     // 旧版表达式内容
  created_at: string;
  updated_at: string;
};

/** 旧版表达式可用的变量名列表（如 close、open、MA20 等） */
export type CustomIndicatorVariableNamesOut = { names: string[] };

/** 内置指标目录（用于 DSL 构建器中的公式引用提示） */
export type BuiltinCatalogItem = {
  name: string;
  display_name: string;
  subs: { name: string; description: string | null }[];
};

/** 指标试算的返回结果（展示最近几日的计算值和错误信息） */
export type UserIndicatorValidateOut = {
  ok: boolean;
  message: string;
  sample_rows: {
    trade_date: string;
    value?: number | null;                    // 旧版单值
    values?: Record<string, number | null> | null; // DSL 多子线值（key→value）
    error: string | null;                     // 该日计算是否出错
    diagnostics?: { code?: string; sub_key?: string; detail?: string; trade_date?: string | null }[] | null;
  }[];
  error_detail: string | null;
  report_keys?: string[] | null;  // 试算报告中显示哪些子线列
};

/** 获取旧版表达式可用的变量名列表（用于 DSL 编辑器的自动补全提示） */
export async function fetchCustomIndicatorVariableNames() {
  const { data } = await api.get<CustomIndicatorVariableNamesOut>("/indicators/custom/variable-names");
  return data;
}

/** 获取内置指标目录（用于 DSL 构建器中引用内置子线时的选项） */
export async function fetchBuiltinIndicatorCatalog() {
  const { data } = await api.get<BuiltinCatalogItem[]>("/indicators/custom/builtin-catalog");
  return data;
}

/** 获取当前用户所有已保存的自定义指标 */
export async function fetchCustomIndicators() {
  const { data } = await api.get<UserIndicatorOut[]>("/indicators/custom");
  return data;
}

/**
 * 新建自定义指标
 * @param body.code          - 英文唯一标识（创建后不可改）
 * @param body.definition    - DSL 模式时传入（JSON 结构）
 * @param body.expr          - 旧版表达式模式时传入
 * @param body.trial_ts_code - 保存前用于试算的股票代码
 */
export async function createCustomIndicator(body: {
  code: string;
  display_name: string;
  description?: string | null;
  definition?: Record<string, unknown>;
  expr?: string | null;
  trial_ts_code?: string;
}) {
  const { data } = await api.post<UserIndicatorOut>("/indicators/custom", body);
  return data;
}

/**
 * 更新已有自定义指标（不可修改 code）
 * @param id   - 指标 ID
 * @param body - 要更新的字段（只传需要改的字段）
 */
export async function patchCustomIndicator(
  id: number,
  body: {
    display_name?: string;
    description?: string | null;
    definition?: Record<string, unknown>;
    expr?: string | null;
    trial_ts_code?: string;
  },
) {
  const { data } = await api.patch<UserIndicatorOut>(`/indicators/custom/${id}`, body);
  return data;
}

/** 删除自定义指标（不可恢复） */
export async function deleteCustomIndicator(id: number) {
  const { data } = await api.delete<{ ok: boolean }>(`/indicators/custom/${id}`);
  return data;
}

/**
 * 试算旧版表达式（不保存，只验证公式是否正确）
 * @param body.expr       - 表达式字符串
 * @param body.ts_code    - 用哪只股票试算（需已同步）
 * @param body.trade_date - 试算截止日期（可选，默认最近日期）
 */
export async function validateCustomIndicatorExpr(body: {
  expr: string;
  ts_code: string;
  trade_date?: string;
}) {
  const { data } = await api.post<UserIndicatorValidateOut>("/indicators/custom/validate-expr", body);
  return data;
}

/**
 * 试算已保存的自定义指标（用于编辑后验证）
 * @param id   - 指标 ID
 * @param body.ts_code - 用哪只股票试算
 */
export async function validateSavedCustomIndicator(
  id: number,
  body: { ts_code: string; trade_date?: string },
) {
  const { data } = await api.post<UserIndicatorValidateOut>(`/indicators/custom/${id}/validate`, body);
  return data;
}

/**
 * 试算 DSL 指标定义（保存前验证，不需要先保存）
 * @param body.definition - DSL 定义对象
 * @param body.ts_code    - 用哪只股票试算
 */
export async function validateCustomIndicatorDefinition(body: {
  definition: Record<string, unknown>;
  ts_code: string;
  trade_date?: string;
}) {
  const { data } = await api.post<UserIndicatorValidateOut>("/indicators/custom/validate-definition", body);
  return data;
}

// ── 股票复盘 ─────────────────────────────────────────────────

/** 三大股指卡片数据（上证、深证、创业板） */
export type ReplayIndexCard = {
  ts_code: string;
  name: string;
  close: number;
  pct_change: number | null;  // 当日涨跌幅
  amount: number;             // 成交额
  data_ok: boolean;           // 是否有有效数据
  message: string | null;     // 无数据时的说明
};

/** 涨跌幅分布的一个区间 */
export type ReplayBucket = {
  key: string;    // 机器标识（如 "-10to-7"）
  label: string;  // 显示标签（如 "-10%~-7%"）
  count: number;  // 该区间内的股票数量
};

/** 复盘中单只股票的数据行（用于散点图等） */
export type ReplayStockRow = {
  ts_code: string;
  name: string | null;
  pct_change: number;
  close: number;
  turnover_rate: number | null;
  bucket: string;              // 所属涨跌幅区间 key
};

/** 单日复盘接口的完整返回结构 */
export type ReplayDailyOut = {
  trade_date: string;               // 实际使用的交易日
  latest_bar_date: string | null;   // 本地最新日线日期（提示数据时效）
  universe_note: string;            // 数据覆盖说明（如"已覆盖 4950 只"）
  up_count: number;                 // 上涨家数
  down_count: number;               // 下跌家数
  flat_count: number;               // 平盘家数
  limit_up_count: number;           // 涨停家数
  limit_down_count: number;         // 跌停家数
  buckets: ReplayBucket[];          // 涨跌幅分布区间
  turnover_avg_up: number | null;   // 上涨股平均换手率
  turnover_avg_down: number | null; // 下跌股平均换手率
  indices: ReplayIndexCard[];       // 三大股指卡片
  stocks: ReplayStockRow[];         // 股票明细列表
};

/**
 * 获取单日市场情绪复盘数据
 * @param params.trade_date - 指定交易日（不传则后端自动取最新日期）
 * @param params.list_limit - 返回股票明细的最大数量
 */
export async function fetchReplayDaily(params?: { trade_date?: string; list_limit?: number }) {
  const { data } = await api.get<ReplayDailyOut>("/replay/daily", { params: params ?? {} });
  return data;
}

// ── 个股列表（数据看板）───────────────────────────────────────

/** 个股列表支持的排序字段 */
export type DailyUniverseSort =
  | "ts_code"
  | "pct_change"
  | "close"
  | "volume"
  | "amount"
  | "turnover_rate";

/**
 * 个股列表的筛选参数（所有字段均可选，数值区间上下界填反时后端自动交换）
 * code_contains/name_contains 等是模糊匹配；min/max 是数值区间
 */
export type DailyUniverseFilterParams = {
  code_contains?: string;
  name_contains?: string;
  market_contains?: string;
  exchange_contains?: string;
  pct_min?: number;
  pct_max?: number;
  open_min?: number;
  open_max?: number;
  high_min?: number;
  high_max?: number;
  low_min?: number;
  low_max?: number;
  close_min?: number;
  close_max?: number;
  volume_min?: number;
  volume_max?: number;
  amount_min?: number;
  amount_max?: number;
  turnover_min?: number;
  turnover_max?: number;
};

/** 个股列表的单行数据（仅包含行情字段，不含同步状态） */
export type DailyUniverseRow = {
  ts_code: string;
  name: string | null;
  market: string | null;
  exchange: string | null;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover_rate: number | null;
  pct_change: number | null;
};

/** 个股列表接口的分页返回结构 */
export type DailyUniverseOut = {
  trade_date: string | null;        // 实际使用的交易日
  latest_bar_date: string | null;   // 本地最新日线日期
  total: number;                    // 当前筛选条件下的总记录数
  page: number;
  page_size: number;
  items: DailyUniverseRow[];
};

/**
 * 获取指定日全市场个股行情表
 * @param params.trade_date - 指定交易日（不传则用最新日期）
 * @param params.page       - 页码（从 1 开始）
 * @param params.page_size  - 每页条数
 * @param params.sort       - 排序字段
 * @param params.order      - asc 升序 / desc 降序
 * @param params...         - 其余为 DailyUniverseFilterParams 筛选参数
 */
export async function fetchDailyUniverse(
  params: {
    trade_date?: string;
    page?: number;
    page_size?: number;
    sort?: DailyUniverseSort;
    order?: "asc" | "desc";
  } & DailyUniverseFilterParams,
) {
  const { data } = await api.get<DailyUniverseOut>("/dashboard/daily-stocks", { params });
  return data;
}

// ── 回测 ─────────────────────────────────────────────────────

/** 回测请求体 */
export type BacktestRunIn = {
  start_date: string;
  end_date: string;
  user_indicator_id: number;
  sub_key?: string | null;
  buy_op: string;
  buy_threshold: number;
  sell_op: string;
  sell_threshold: number;
  initial_capital: number;
  max_positions: number;
  max_scan?: number;
};

/** 资金曲线中的一个数据点 */
export type BacktestEquityPoint = {
  date: string;
  equity: number;
  drawdown_pct: number;
};

/** 回测交易记录中的一笔交易 */
export type BacktestTradeRow = {
  ts_code: string;
  name: string | null;
  buy_date: string;
  buy_price: number;
  shares: number;
  sell_date: string | null;
  sell_price: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  buy_trigger_val: number | null;
  sell_trigger_val: number | null;
};

/** 回测完整结果 */
export type BacktestRunOut = {
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_equity: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  total_trades: number;
  win_rate: number | null;
  scanned_stocks: number;
  equity_curve: BacktestEquityPoint[];
  trades: BacktestTradeRow[];
  note: string | null;
  // 高级绩效指标
  annualized_return: number | null;
  sharpe_ratio: number | null;
  calmar_ratio: number | null;
  profit_factor: number | null;
  avg_win_pct: number | null;
  avg_loss_pct: number | null;
  max_win_pct: number | null;
  max_loss_pct: number | null;
  avg_holding_days: number | null;
  total_win: number;
  total_loss: number;
};

/**
 * 执行一次条件选股回测
 * 注意：回测可能耗时较长（全市场 × 多日），timeout 设置为 5 分钟
 */
export async function runBacktest(body: BacktestRunIn): Promise<BacktestRunOut> {
  const { data } = await api.post<BacktestRunOut>("/backtest/run", body, {
    timeout: 300_000,
  });
  return data;
}

/** 回测交易验证图：K线 + 指标子线 */
export type TradeChartBarPoint = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type TradeChartIndicatorPoint = {
  time: string;
  value: number | null;
};

export type TradeChartOut = {
  bars: TradeChartBarPoint[];
  indicator: TradeChartIndicatorPoint[];
  sub_key: string;
  sub_display_name: string;
};

export async function fetchTradeChart(params: {
  ts_code: string;
  user_indicator_id: number;
  sub_key: string;
  start_date: string;
  end_date: string;
}): Promise<TradeChartOut> {
  const { data } = await api.get<TradeChartOut>("/backtest/trade-chart", { params });
  return data;
}

// ── 回测历史记录 ─────────────────────────────────────────────────
// （每次在「开始回测」页面执行后自动保存，此处只负责读取和删除）

/** 回测历史列表中的一行摘要（不含完整资金曲线和交易记录） */
export type BacktestRecordItem = {
  id: number;
  created_at: string;             // 执行时间（ISO 格式）
  start_date: string;             // 回测起始日
  end_date: string;               // 回测截止日
  indicator_name: string;         // 指标显示名（冗余存储）
  indicator_code: string;         // 指标英文标识
  user_indicator_id: number | null;
  sub_key: string | null;         // 参与回测的子线
  buy_op: string;                 // 买入比较运算符
  buy_threshold: number;          // 买入阈值
  sell_op: string;                // 卖出比较运算符
  sell_threshold: number;         // 卖出阈值
  initial_capital: number;        // 初始资金（元）
  max_positions: number;          // 最大持仓只数
  total_return_pct: number;       // 总收益率（%）
  max_drawdown_pct: number;       // 最大回撤（%）
  total_trades: number;           // 总交易笔数
  win_rate: number | null;        // 胜率（%）
  annualized_return: number | null;  // 年化收益率（%）
  sharpe_ratio: number | null;    // 夏普比率
};

/** 回测历史详情（包含完整结果：资金曲线 + 交易记录，从 result_json 反序列化） */
export type BacktestRecordDetail = BacktestRecordItem & {
  result: BacktestRunOut | null;  // 完整回测结果（含资金曲线和交易明细）
};

/**
 * 获取回测历史记录列表（按执行时间倒序）
 * @param params.page      - 页码（从 1 开始）
 * @param params.page_size - 每页条数（5~100）
 */
export async function fetchBacktestRecords(params?: { page?: number; page_size?: number }) {
  const { data } = await api.get<BacktestRecordItem[]>("/backtest/records", { params });
  return data;
}

/** 获取单条回测历史的详情（含完整资金曲线与交易记录） */
export async function fetchBacktestRecordDetail(id: number) {
  const { data } = await api.get<BacktestRecordDetail>(`/backtest/records/${id}`);
  return data;
}

/** 删除指定的回测历史记录（不可恢复） */
export async function deleteBacktestRecord(id: number) {
  const { data } = await api.delete<{ ok: boolean }>(`/backtest/records/${id}`);
  return data;
}

// ── 大V情绪仪表盘 ────────────────────────────────────────────

/** 情绪趋势中的单日数据点 */
export type SentimentTrendPoint = {
  trade_date: string;
  up_count: number;
  down_count: number;
  flat_count: number;
  limit_up_count: number;
  limit_down_count: number;
  total: number;
  up_ratio: number;         // 上涨占比（%）
  limit_up_ratio: number;   // 涨停占上涨比例（%）
  sentiment_score: number;  // 综合情绪分（0~100）
};

/** GET /api/replay/sentiment-trend 的返回体 */
export type SentimentTrendOut = {
  days: number;
  points: SentimentTrendPoint[];
  latest_date: string | null;
};

/**
 * 获取近N日市场情绪趋势数据（用于大V情绪仪表盘）
 * @param params.days - 最近多少个交易日（5~120，默认 60）
 */
export async function fetchSentimentTrend(params?: { days?: number }) {
  const { data } = await api.get<SentimentTrendOut>("/replay/sentiment-trend", { params: params ?? {} });
  return data;
}

// ── 大V看板（Mr. Dang ABCD分类 + 预期股息率）─────────────────────────────

/** 大V看板中一只股票的完整信息 */
export type DavStockOut = {
  ts_code: string;
  name: string | null;
  dav_class: "A" | "B" | "C" | "D" | null;
  latest_price: number | null;
  manual_payout_ratio: number | null;   // 近两年平均派息率（%）
  manual_eps: number | null;            // 预测全年 EPS（元）
  expected_yield: number | null;        // 预期股息率（%），三项数据均有时自动计算
  data_complete: boolean;               // true = 可自动计算预期股息率
  notes: string | null;                 // 纠正备注
};

/** 添加股票到看板的请求体 */
export type DavStockIn = {
  ts_code: string;
  dav_class?: "A" | "B" | "C" | "D" | null;
  manual_payout_ratio?: number | null;
  manual_eps?: number | null;
  notes?: string | null;
};

/** 更新看板股票的请求体（所有字段可选） */
export type DavStockPatch = {
  dav_class?: "A" | "B" | "C" | "D" | null;
  manual_payout_ratio?: number | null;
  manual_eps?: number | null;
  notes?: string | null;
};

/** 搜索本地股票时返回的简单条目 */
export type DavSearchItem = { ts_code: string; name: string | null };

/** 获取全部大V看板股票 */
export async function fetchDavStocks() {
  const { data } = await api.get<DavStockOut[]>("/dav/stocks");
  return data;
}

/** 搜索本地已知股票（用于添加时下拉选） */
export async function searchDavStocks(q: string) {
  const { data } = await api.get<DavSearchItem[]>("/dav/stocks/search", { params: { q } });
  return data;
}

/** 添加一只股票到大V看板 */
export async function addDavStock(body: DavStockIn) {
  const { data } = await api.post<DavStockOut>("/dav/stocks", body);
  return data;
}

/** 更新看板中某只股票的信息 */
export async function updateDavStock(ts_code: string, body: DavStockPatch) {
  const { data } = await api.patch<DavStockOut>(`/dav/stocks/${encodeURIComponent(ts_code)}`, body);
  return data;
}

/** 从大V看板移除一只股票 */
export async function removeDavStock(ts_code: string) {
  await api.delete(`/dav/stocks/${encodeURIComponent(ts_code)}`);
}

// ── 自选股池 ──────────────────────────────────────────────────────────────────

export type WatchlistItem = {
  ts_code: string;
  name: string | null;
  note: string | null;
  created_at: string;
};

/** 获取全部自选股（按加入时间倒序） */
export async function fetchWatchlist() {
  const { data } = await api.get<WatchlistItem[]>("/watchlist/");
  return data;
}

/** 添加一只股票到自选股池（已存在则更新 name/note） */
export async function addToWatchlist(ts_code: string, name?: string | null, note?: string | null) {
  const { data } = await api.post<WatchlistItem>("/watchlist/", { ts_code, name, note });
  return data;
}

/** 从自选股池移除一只股票 */
export async function removeFromWatchlist(ts_code: string) {
  await api.delete(`/watchlist/${encodeURIComponent(ts_code)}`);
}

// ── 鉴权 ─────────────────────────────────────────────────────────────────────

export type UserInfo = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
};

export type TokenOut = {
  access_token: string;
  token_type: string;
};

/** 登录：返回 JWT token */
export async function login(username: string, password: string): Promise<TokenOut> {
  const { data } = await api.post<TokenOut>("/auth/login", { username, password });
  return data;
}

/** 获取当前登录用户信息（用于验证 token 有效性） */
export async function fetchCurrentUser(): Promise<UserInfo> {
  const { data } = await api.get<UserInfo>("/auth/me");
  return data;
}

/** [管理员] 获取全部用户列表 */
export async function fetchUsers(): Promise<UserInfo[]> {
  const { data } = await api.get<UserInfo[]>("/auth/users");
  return data;
}

/** [管理员] 创建新用户 */
export async function createUser(body: { username: string; password: string; is_admin?: boolean }): Promise<UserInfo> {
  const { data } = await api.post<UserInfo>("/auth/users", body);
  return data;
}

/** [管理员] 修改用户（重置密码/启停账号/升降权限） */
export async function updateUser(
  id: number,
  body: { password?: string; is_active?: boolean; is_admin?: boolean },
): Promise<UserInfo> {
  const { data } = await api.patch<UserInfo>(`/auth/users/${id}`, body);
  return data;
}

/** [管理员] 开关开放注册 */
export async function toggleRegistration(allow: boolean): Promise<{ allow_registration: boolean }> {
  const { data } = await api.patch<{ allow_registration: boolean }>("/auth/settings/registration", { allow });
  return data;
}
