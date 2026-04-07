/**
 * 和后端的「对照表」：下面每个函数对应后端一条接口（路径都在 /api 下）。
 * 页面里只调用这里，不要到处手写网址，以后改路径只改这一文件。
 *
 * 和页面对应关系（按需搜函数名即可）：
 * - K 线：fetchBars、fetchSymbols
 * - 同步任务：fetchSyncJob、triggerSyncRun、fetchSyncBySelection、pauseSyncRun、resumeSyncRun、cancelSyncRun、token…
 * - 数据后台：fetchDataCenter、syncStockListMeta、fetchIndexCandidates、applyIndexMetaSelection…
 * - 指标库：fetchIndicators、fetchIndicatorDetail、seedIndicators；自定义指标 custom* 系列
 * - 数据看板·个股列表：fetchDailyUniverse
 * - （回测已下线，V0.0.3 按新指标体系重做）
 */
import axios from "axios";

export const api = axios.create({
  baseURL: "/api",
  timeout: 120_000,
});

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

export type SymbolRow = {
  id: number;
  ts_code: string;
  name: string | null;
};

export type BarPoint = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover_rate_avg: number | null;
  consecutive_limit_up_days: number | null;
  consecutive_limit_down_days: number | null;
  consecutive_up_days: number | null;
  consecutive_down_days: number | null;
};

export type Interval = "1d" | "1w" | "1M" | "1Q" | "1y";

export type SyncJob = {
  id: number;
  cron_expr: string;
  enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
};

export type SyncRun = {
  id: number;
  started_at: string;
  finished_at: string | null;
  trigger: string;
  status: string;
  message: string | null;
  log_path: string | null;
  /** 用户已点暂停，工作或将在下一只标的处阻塞 */
  pause_requested?: boolean;
  cancel_requested?: boolean;
};

// ---------- 股票池（多页面选代码） ----------
export async function fetchSymbols() {
  const { data } = await api.get<SymbolRow[]>("/symbols");
  return data;
}

export type AdjType = "none" | "qfq" | "hfq";

// ---------- K 线 ----------
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

export type CustomIndicatorSeriesOut = {
  ts_code: string;
  user_indicator_id: number;
  sub_key: string;
  display_name: string;
  points: { time: string; value: number | null }[];
};

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

export type ScreeningStockRow = {
  ts_code: string;
  name: string | null;
  close: number;
  pct_change: number | null;
  indicator_value: number;
};

export type ScreeningRunOut = {
  trade_date: string;
  user_indicator_id: number | null;
  sub_key: string | null;
  compare_op: string | null;
  threshold: number | null;
  scanned: number;
  matched: number;
  note: string | null;
  items: ScreeningStockRow[];
};

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

// ---------- 同步：任务、运行记录、按股拉数 ----------
export async function fetchSyncJob() {
  const { data } = await api.get<SyncJob>("/sync/job");
  return data;
}

export async function updateSyncJob(body: {
  cron_expr?: string;
  enabled?: boolean;
}) {
  const { data } = await api.put<SyncJob>("/sync/job", body);
  return data;
}

export async function triggerSyncRun() {
  const { data } = await api.post<SyncRun>("/sync/run");
  return data;
}

export async function fetchSyncRuns(limit = 20) {
  const { data } = await api.get<SyncRun[]>("/sync/runs", { params: { limit } });
  return data;
}

export async function pauseSyncRun(runId: number) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/pause`);
  return data;
}

export async function resumeSyncRun(runId: number) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/resume`);
  return data;
}

export async function cancelSyncRun(runId: number, opts?: { force?: boolean }) {
  const { data } = await api.post<SyncRun>(`/sync/runs/${runId}/cancel`, {}, {
    params: opts?.force ? { force: true } : {},
  });
  return data;
}

export type TushareSymbol = {
  ts_code: string;
  name: string | null;
};

export async function fetchAllASymbols() {
  const { data } = await api.get<TushareSymbol[]>("/tushare/symbols");
  return data;
}

export async function fetchSyncBySelection(body: {
  ts_codes: string[];
  start_date?: string; // YYYY-MM-DD
  end_date: string; // YYYY-MM-DD
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch", body);
  return data;
}

/** 全市场拉取：后端按元数据全部个股取码（与数据池一致），日期规则与 fetchSyncBySelection 一致 */
export async function fetchSyncAllMarket(body: {
  start_date?: string;
  end_date: string;
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch-all", body);
  return data;
}

/** 数据池中已登记的全部指数：请求体与 fetchSyncAllMarket 相同，后端走 index_daily */
export async function fetchSyncAllIndexPool(body: {
  start_date?: string;
  end_date: string;
  from_listing?: boolean;
}) {
  const { data } = await api.post<SyncRun>("/sync/fetch-all-index", body);
  return data;
}

export type UniverseSyncOut = {
  stock_count: number;
  index_count: number;
  total: number;
  from_cache: boolean;
  last_sync_date?: string | null;
  inserted_stocks?: number;
  updated_stocks?: number;
};

export type DataCenterRow = {
  ts_code: string;
  name: string | null;
  asset_type: string;
  list_date: string | null;
  market: string | null;
  exchange: string | null;
  synced_once: boolean;
  first_bar_date: string | null;
  last_bar_date: string | null;
  bar_count: number;
  adj_factor_count: number;
  adj_factor_coverage_ratio: number;
  adj_factor_synced: boolean;
};

/** 增量更新上市股票元数据（含市场类别、交易所） */
export async function syncStockListMeta() {
  const { data } = await api.post<UniverseSyncOut>("/sync/stock-list");
  return data;
}

/** @deprecated 请用 syncStockListMeta；仍指向同一接口 */
export async function syncUniverseMeta(_force = false) {
  return syncStockListMeta();
}

export type IndexCandidateRow = {
  ts_code: string;
  name: string | null;
  market: string | null;
  publisher: string | null;
  list_date: string | null;
};

export async function fetchIndexCandidates(params?: { market?: string; limit?: number }) {
  const { data } = await api.get<IndexCandidateRow[]>("/sync/index-candidates", { params });
  return data;
}

export async function applyIndexMetaSelection(items: { ts_code: string; name?: string | null; list_date?: string | null }[]) {
  const { data } = await api.post<{ added: number; skipped: number }>("/sync/index-meta/apply", { items });
  return data;
}

export async function fetchDataCenter(limit = 500) {
  const { data } = await api.get<DataCenterRow[]>("/sync/data-center", { params: { limit } });
  return data;
}

export type SymbolDailyRow = {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover_rate: number | null;
  has_adj_factor: boolean;
};

export type SymbolDailyPage = { total: number; items: SymbolDailyRow[] };

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

export async function triggerSingleDaySync(ts_code: string, trade_date: string) {
  const { data } = await api.post<SyncRun>("/sync/single-day", { ts_code, trade_date });
  return data;
}

export type TushareTokenStatus = {
  hasRuntime: boolean;
  hasDb?: boolean;
  hasEnv: boolean;
  configured: boolean;
  stockListLastSyncDate?: string | null;
};

// ---------- 管理：Tushare token（主要在同步页） ----------
export async function fetchTushareTokenStatus() {
  const { data } = await api.get<TushareTokenStatus>("/admin/tushare/token-status");
  return data;
}

export async function setTushareToken(token: string) {
  const { data } = await api.post<{ ok: boolean }>("/admin/tushare/token", { token });
  return data;
}



// ---------- 指标库 ----------
export type IndicatorListItem = {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  params_count: number;
  sub_count: number;
};

export type IndicatorDetail = {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  params: { id: number; name: string; description: string | null; default_value: string | null }[];
  sub_indicators: { id: number; name: string; description: string | null; can_be_price: boolean }[];
};

export async function fetchIndicators() {
  const { data } = await api.get<IndicatorListItem[]>("/indicators");
  return data;
}

export async function seedIndicators(force = true) {
  const { data } = await api.post<{ message: string }>(`/indicators/seed?force=${force}`);
  return data;
}

export async function fetchIndicatorDetail(id: number) {
  const { data } = await api.get<IndicatorDetail>(`/indicators/${id}`);
  return data;
}

// ---------- 用户自定义指标（PRD DSL + 旧版 expr）----------
export type UserIndicatorKind = "dsl" | "legacy";

export type UserIndicatorOut = {
  id: number;
  code: string;
  display_name: string;
  description: string | null;
  kind: UserIndicatorKind;
  definition: Record<string, unknown> | null;
  expr: string | null;
  created_at: string;
  updated_at: string;
};

export type CustomIndicatorVariableNamesOut = { names: string[] };

export type BuiltinCatalogItem = {
  name: string;
  display_name: string;
  subs: { name: string; description: string | null }[];
};

export type UserIndicatorValidateOut = {
  ok: boolean;
  message: string;
  sample_rows: {
    trade_date: string;
    value?: number | null;
    values?: Record<string, number | null> | null;
    error: string | null;
    diagnostics?: { code?: string; sub_key?: string; detail?: string; trade_date?: string | null }[] | null;
  }[];
  error_detail: string | null;
  report_keys?: string[] | null;
};

export async function fetchCustomIndicatorVariableNames() {
  const { data } = await api.get<CustomIndicatorVariableNamesOut>("/indicators/custom/variable-names");
  return data;
}

export async function fetchBuiltinIndicatorCatalog() {
  const { data } = await api.get<BuiltinCatalogItem[]>("/indicators/custom/builtin-catalog");
  return data;
}

export async function fetchCustomIndicators() {
  const { data } = await api.get<UserIndicatorOut[]>("/indicators/custom");
  return data;
}

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

export async function deleteCustomIndicator(id: number) {
  const { data } = await api.delete<{ ok: boolean }>(`/indicators/custom/${id}`);
  return data;
}

export async function validateCustomIndicatorExpr(body: {
  expr: string;
  ts_code: string;
  trade_date?: string;
}) {
  const { data } = await api.post<UserIndicatorValidateOut>("/indicators/custom/validate-expr", body);
  return data;
}

export async function validateSavedCustomIndicator(
  id: number,
  body: { ts_code: string; trade_date?: string },
) {
  const { data } = await api.post<UserIndicatorValidateOut>(`/indicators/custom/${id}/validate`, body);
  return data;
}

export async function validateCustomIndicatorDefinition(body: {
  definition: Record<string, unknown>;
  ts_code: string;
  trade_date?: string;
}) {
  const { data } = await api.post<UserIndicatorValidateOut>("/indicators/custom/validate-definition", body);
  return data;
}

// ---------- 股票复盘 V2.0.1 ----------
export type ReplayIndexCard = {
  ts_code: string;
  name: string;
  close: number;
  pct_change: number | null;
  amount: number;
  data_ok: boolean;
  message: string | null;
};

export type ReplayBucket = { key: string; label: string; count: number };

export type ReplayStockRow = {
  ts_code: string;
  name: string | null;
  pct_change: number;
  close: number;
  turnover_rate: number | null;
  bucket: string;
};

export type ReplayDailyOut = {
  trade_date: string;
  latest_bar_date: string | null;
  universe_note: string;
  up_count: number;
  down_count: number;
  flat_count: number;
  limit_up_count: number;
  limit_down_count: number;
  buckets: ReplayBucket[];
  turnover_avg_up: number | null;
  turnover_avg_down: number | null;
  indices: ReplayIndexCard[];
  stocks: ReplayStockRow[];
};

/** 单日复盘；不传 trade_date 时后端用本地最新交易日 */
export async function fetchReplayDaily(params?: { trade_date?: string; list_limit?: number }) {
  const { data } = await api.get<ReplayDailyOut>("/replay/daily", { params: params ?? {} });
  return data;
}

export type DailyUniverseSort =
  | "ts_code"
  | "pct_change"
  | "close"
  | "volume"
  | "amount"
  | "turnover_rate";

/** 与 GET /dashboard/daily-stocks 查询参数对齐的可选筛选（均为可选，区间上下界填反时后端会自动交换） */
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

export type DailyUniverseOut = {
  trade_date: string | null;
  latest_bar_date: string | null;
  total: number;
  page: number;
  page_size: number;
  items: DailyUniverseRow[];
};

/** 指定日全市场个股行情表（无同步元数据）；不传 trade_date 时用本地最新交易日 */
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
