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
  enabled: boolean;
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
};

export async function fetchSymbols(enabled?: boolean) {
  const { data } = await api.get<SymbolRow[]>("/symbols", {
    params: enabled === undefined ? {} : { enabled },
  });
  return data;
}

export type AdjType = "none" | "qfq" | "hfq";

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

export type UniverseSyncOut = {
  stock_count: number;
  index_count: number;
  total: number;
  from_cache: boolean;
  last_sync_date?: string | null;
};

export type DataCenterRow = {
  ts_code: string;
  name: string | null;
  asset_type: string;
  list_date: string | null;
  synced_once: boolean;
  first_bar_date: string | null;
  last_bar_date: string | null;
  bar_count: number;
  adj_factor_count: number;
  adj_factor_coverage_ratio: number;
  adj_factor_synced: boolean;
};

export async function syncUniverseMeta(force = false) {
  const { data } = await api.post<UniverseSyncOut>("/sync/universe/sync", null, { params: { force } });
  return data;
}

export async function fetchDataCenter(limit = 500) {
  const { data } = await api.get<DataCenterRow[]>("/sync/data-center", { params: { limit } });
  return data;
}

export type TushareTokenStatus = {
  hasRuntime: boolean;
  hasDb?: boolean;
  hasEnv: boolean;
  configured: boolean;
  stockListLastSyncDate?: string | null;
};

export async function fetchTushareTokenStatus() {
  const { data } = await api.get<TushareTokenStatus>("/admin/tushare/token-status");
  return data;
}

export async function setTushareToken(token: string) {
  const { data } = await api.post<{ ok: boolean }>("/admin/tushare/token", { token });
  return data;
}

export type BuyOnceBacktestRequest = {
  ts_code: string;
  start_date: string;
  end_date: string;
  buy_date: string;
  buy_price: number;
  buy_qty: number;
  initial_cash: number;
};

export type BacktestDailyPoint = {
  trade_date: string;
  close: number;
  stock_value: number;
  cash_value: number;
  total_asset: number;
  daily_pnl: number;
  cum_return: number;
};

export type BuyOnceBacktestResponse = {
  ts_code: string;
  start_date: string;
  end_date: string;
  buy_date: string;
  buy_price: number;
  buy_qty: number;
  initial_cash: number;
  remaining_cash: number;
  max_drawdown: number;
  daily: BacktestDailyPoint[];
};

export async function runBuyOnceBacktest(body: BuyOnceBacktestRequest) {
  const { data } = await api.post<BuyOnceBacktestResponse>("/backtest/buy-once", body);
  return data;
}

export type BuySellBacktestRequest = {
  ts_code: string;
  start_date: string;
  end_date: string;
  buy_date: string;
  buy_price: number;
  buy_qty: number;
  initial_cash: number;
  adj?: AdjType;
  sell_target_price?: number;
  sell_target_return?: number;
  sell_target_date?: string;
  sell_logic: "or" | "and";
};

export type BuySellBacktestResponse = {
  ts_code: string;
  start_date: string;
  end_date: string;
  buy_date: string;
  sell_date: string | null;
  sell_price: number | null;
  sell_reason: string | null;
  buy_price: number;
  buy_qty: number;
  initial_cash: number;
  remaining_cash: number;
  max_drawdown: number;
  daily: BacktestDailyPoint[];
};

export async function runBuySellBacktest(body: BuySellBacktestRequest) {
  const { data } = await api.post<BuySellBacktestResponse>("/backtest/buy-sell", body);
  return data;
}

// ---- 指标库 ----
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
  sub_indicators: { id: number; name: string; description: string | null }[];
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

// ---- V1.0.6 条件买入 ----
export type IndicatorRef =
  | { kind: "number"; value: number }
  | { kind: "indicator"; sub_name: string };

export type BuyTimingConfig = {
  time_offset: number;  // 0=当日, -1=T-1交易日, ...
  condition_type: "price" | "indicator";
  price?: number;
  left?: IndicatorRef;
  operator?: "gt" | "eq" | "lt";
  right?: IndicatorRef;
};

export type BuyPriceConfig =
  | { type: "fixed"; fixed_price: number }
  | { type: "indicator"; sub_name: string };

export type BuyQtyConfig =
  | { type: "fixed"; fixed_qty: number }
  | { type: "ratio"; ratio: number };

export type ConditionBuyRequest = {
  ts_code: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  adj: AdjType;
  buy_timing: BuyTimingConfig;
  buy_price: BuyPriceConfig;
  buy_qty: BuyQtyConfig;
  sell_target_price?: number;
  sell_target_return?: number;
  sell_target_date?: string;
  sell_logic: "or" | "and";
};

export type ConditionBuyDailyPoint = {
  trade_date: string;
  close: number;
  holding_qty: number;
  stock_value: number;
  cash_value: number;
  total_asset: number;
  daily_pnl: number;
  cum_return: number;
};

export type ConditionBuyResponse = {
  ts_code: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  remaining_cash: number;
  buy_count: number;
  sell_date: string | null;
  sell_price: number | null;
  sell_reason: string | null;
  max_drawdown: number;
  daily: ConditionBuyDailyPoint[];
};

export async function runConditionBuyBacktest(body: ConditionBuyRequest) {
  const { data } = await api.post<ConditionBuyResponse>("/backtest/condition-buy", body);
  return data;
}
