/**
 * 个股列表页面
 *
 * 功能：按交易日展示全市场所有股票的当日行情数据。
 * 筛选入口内嵌在列头（Excel 风格），点击列头筛选图标弹出输入框。
 */
import {
  Button,
  Card,
  DatePicker,
  Flex,
  Grid,
  Input,
  InputNumber,
  Space,
  Table,
  Typography,
  message,
  theme,
} from "antd";
import { FilterOutlined, SearchOutlined } from "@ant-design/icons";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import type {
  FilterDropdownProps,
  FilterValue,
  SorterResult,
  SortOrder,
  TableCurrentDataSource,
} from "antd/es/table/interface";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  type DailyUniverseFilterParams,
  type DailyUniverseRow,
  type DailyUniverseSort,
  fetchDailyUniverse,
  getApiErrorMessage,
} from "../api/client";
import { FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";

const { Text, Paragraph } = Typography;

function fmtAmount(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
}

function fmtVol(v: number): string {
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return String(Math.round(v));
}

function fmtMV(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`;
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  return `${(v / 1e4).toFixed(2)} 万`;
}

function numOrUndef(raw: unknown): number | undefined {
  if (raw === null || raw === undefined || raw === "") return undefined;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

export default function StockListPage() {
  const { token } = theme.useToken();
  const screens = Grid.useBreakpoint();
  const showOhlcExtras = Boolean(screens.lg);

  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<Dayjs | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortField, setSortField] = useState<DailyUniverseSort>("pct_change");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<DailyUniverseRow[]>([]);
  const [tradeDateStr, setTradeDateStr] = useState<string | null>(null);
  const [latestBar, setLatestBar] = useState<string | null>(null);

  // appliedFilters：已提交给 API 的筛选条件
  const [appliedFilters, setAppliedFilters] = useState<DailyUniverseFilterParams>({});
  // filterInputs：列头下拉框中的草稿值（未提交）
  const [filterInputs, setFilterInputs] = useState<Record<string, unknown>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = picked ? picked.format("YYYY-MM-DD") : undefined;
      const out = await fetchDailyUniverse({
        trade_date: d,
        page,
        page_size: pageSize,
        sort: sortField,
        order: sortOrder,
        ...appliedFilters,
      });
      setItems(out.items);
      setTotal(out.total);
      setTradeDateStr(out.trade_date);
      setLatestBar(out.latest_bar_date);
      if (!picked && out.trade_date) setPicked(dayjs(out.trade_date));
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [picked, page, pageSize, sortField, sortOrder, appliedFilters]);

  useEffect(() => {
    void load();
  }, [load]);

  const sortOrderForCol = (field: DailyUniverseSort): SortOrder | undefined =>
    sortField === field ? (sortOrder === "asc" ? "ascend" : "descend") : undefined;

  // ── 列头筛选 helpers ────────────────────────────────────────────

  /** 文本类筛选下拉框（代码、名称、市场等） */
  const makeTextFilter = (filterKey: keyof DailyUniverseFilterParams) => ({
    filterDropdown: ({ confirm }: FilterDropdownProps) => {
      const val = (filterInputs[filterKey] as string) ?? "";
      const apply = () => {
        const v = (filterInputs[filterKey] as string)?.trim() || undefined;
        setAppliedFilters(f => v ? { ...f, [filterKey]: v } : (({ [filterKey]: _, ...rest }) => rest)(f as Record<string, unknown>) as DailyUniverseFilterParams);
        setPage(1);
        confirm();
      };
      const clear = () => {
        setFilterInputs(p => (({ [filterKey]: _, ...rest }) => rest)(p));
        setAppliedFilters(f => (({ [filterKey]: _, ...rest }) => rest)(f as Record<string, unknown>) as DailyUniverseFilterParams);
        setPage(1);
        confirm();
      };
      return (
        <div style={{ padding: 8, minWidth: 180 }}>
          <Input
            placeholder="输入关键词，回车确认"
            value={val}
            onChange={e => setFilterInputs(p => ({ ...p, [filterKey]: e.target.value }))}
            onPressEnter={apply}
            style={{ display: "block", marginBottom: 8 }}
            allowClear
            onClear={clear}
            autoFocus
          />
          <Space>
            <Button type="primary" size="small" onClick={apply}>确认</Button>
            <Button size="small" onClick={clear}>清除</Button>
          </Space>
        </div>
      );
    },
    filterIcon: <SearchOutlined style={{ color: appliedFilters[filterKey] ? "#1677ff" : undefined }} />,
    filtered: !!appliedFilters[filterKey],
  });

  /** 数值范围筛选下拉框（涨跌幅、收盘价、PE 等） */
  const makeRangeFilter = (minKey: keyof DailyUniverseFilterParams, maxKey: keyof DailyUniverseFilterParams) => ({
    filterDropdown: ({ confirm }: FilterDropdownProps) => {
      const minVal = filterInputs[minKey] as number | null | undefined;
      const maxVal = filterInputs[maxKey] as number | null | undefined;
      const apply = () => {
        const mn = numOrUndef(filterInputs[minKey]);
        const mx = numOrUndef(filterInputs[maxKey]);
        setAppliedFilters(f => {
          const next = { ...f } as Record<string, number | undefined>;
          if (mn !== undefined) next[minKey] = mn; else delete next[minKey];
          if (mx !== undefined) next[maxKey] = mx; else delete next[maxKey];
          return next as DailyUniverseFilterParams;
        });
        setPage(1);
        confirm();
      };
      const clear = () => {
        setFilterInputs(p => { const n = { ...p }; delete n[minKey]; delete n[maxKey]; return n; });
        setAppliedFilters(f => { const n = { ...f }; delete n[minKey]; delete n[maxKey]; return n; });
        setPage(1);
        confirm();
      };
      return (
        <div style={{ padding: 8, minWidth: 160 }}>
          <Space direction="vertical" size={6} style={{ width: "100%" }}>
            <InputNumber
              placeholder="最小值"
              value={minVal ?? null}
              onChange={v => setFilterInputs(p => ({ ...p, [minKey]: v }))}
              style={{ width: "100%" }}
              controls={false}
            />
            <InputNumber
              placeholder="最大值"
              value={maxVal ?? null}
              onChange={v => setFilterInputs(p => ({ ...p, [maxKey]: v }))}
              onPressEnter={apply}
              style={{ width: "100%" }}
              controls={false}
            />
            <Space>
              <Button type="primary" size="small" onClick={apply}>确认</Button>
              <Button size="small" onClick={clear}>清除</Button>
            </Space>
          </Space>
        </div>
      );
    },
    filterIcon: <FilterOutlined style={{ color: (appliedFilters[minKey] !== undefined || appliedFilters[maxKey] !== undefined) ? "#1677ff" : undefined }} />,
    filtered: appliedFilters[minKey] !== undefined || appliedFilters[maxKey] !== undefined,
  });

  // ── 列定义 ──────────────────────────────────────────────────────

  const columns: ColumnsType<DailyUniverseRow> = useMemo(() => {
    const ohlc: ColumnsType<DailyUniverseRow> = showOhlcExtras
      ? [
          { title: "开盘", dataIndex: "open", width: 80, align: "right" as const, render: (v: number) => v.toFixed(2), ...makeRangeFilter("open_min", "open_max") },
          { title: "高", dataIndex: "high", width: 72, align: "right" as const, render: (v: number) => v.toFixed(2), ...makeRangeFilter("high_min", "high_max") },
          { title: "低", dataIndex: "low", width: 72, align: "right" as const, render: (v: number) => v.toFixed(2), ...makeRangeFilter("low_min", "low_max") },
        ]
      : [];

    return [
      {
        title: "代码", dataIndex: "ts_code", width: 108, fixed: "left" as const,
        sorter: true, sortOrder: sortOrderForCol("ts_code"),
        render: (c: string) => <Link to={`/?ts_code=${encodeURIComponent(c)}`} style={{ color: token.colorPrimary }}>{c}</Link>,
        ...makeTextFilter("code_contains"),
      },
      { title: "名称", dataIndex: "name", width: 100, ellipsis: true, ...makeTextFilter("name_contains") },
      { title: "市场", dataIndex: "market", width: 72, ellipsis: true, ...makeTextFilter("market_contains") },
      {
        title: "涨跌幅%", dataIndex: "pct_change", width: 96, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("pct_change"),
        render: (v: number | null) =>
          v == null ? "—" : (
            <Text strong style={{ color: v > 0 ? RISE_COLOR : v < 0 ? FALL_COLOR : FLAT_COLOR }}>
              {v > 0 ? "+" : ""}{v.toFixed(2)}
            </Text>
          ),
        ...makeRangeFilter("pct_min", "pct_max"),
      },
      {
        title: "收盘", dataIndex: "close", width: 88, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("close"),
        render: (v: number) => v.toFixed(2),
        ...makeRangeFilter("close_min", "close_max"),
      },
      ...ohlc,
      {
        title: "换手%", dataIndex: "turnover_rate", width: 88, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("turnover_rate"),
        render: (v: number | null) => (v == null ? "—" : v.toFixed(2)),
        ...makeRangeFilter("turnover_min", "turnover_max"),
      },
      {
        title: "PE(TTM)", dataIndex: "pe_ttm", width: 88, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("pe_ttm"),
        render: (v: number | null) => (v == null ? "—" : v.toFixed(1)),
        ...makeRangeFilter("pe_min", "pe_max"),
      },
      {
        title: "PB", dataIndex: "pb", width: 72, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("pb"),
        render: (v: number | null) => (v == null ? "—" : v.toFixed(2)),
        ...makeRangeFilter("pb_min", "pb_max"),
      },
      {
        title: "总市值", dataIndex: "total_mv", width: 100, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("total_mv"),
        render: (v: number | null) => fmtMV(v),
      },
      {
        title: "成交量", dataIndex: "volume", width: 100, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("volume"),
        render: (v: number) => fmtVol(v),
        ...makeRangeFilter("volume_min", "volume_max"),
      },
      {
        title: "成交额", dataIndex: "amount", width: 100, align: "right" as const,
        sorter: true, sortOrder: sortOrderForCol("amount"),
        render: (v: number) => fmtAmount(v),
        ...makeRangeFilter("amount_min", "amount_max"),
      },
    ];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortField, sortOrder, token.colorPrimary, showOhlcExtras, filterInputs, appliedFilters]);

  const onTableChange = (
    pag: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<DailyUniverseRow> | SorterResult<DailyUniverseRow>[],
    extra: TableCurrentDataSource<DailyUniverseRow>,
  ) => {
    if (pag.current != null) setPage(pag.current);
    if (pag.pageSize != null) setPageSize(pag.pageSize);
    if (extra.action !== "sort") return;

    const one = Array.isArray(sorter) ? sorter[0] : sorter;
    if (!one) return;

    const rawField = one.field != null ? one.field : sortField;
    const field = String(Array.isArray(rawField) ? rawField[0] : rawField) as DailyUniverseSort;
    const allowed: DailyUniverseSort[] = [
      "ts_code", "pct_change", "close", "volume", "amount", "turnover_rate",
      "pe_ttm", "pb", "total_mv",
    ];
    if (!allowed.includes(field)) return;

    if (one.order === "ascend") {
      setSortField(field); setSortOrder("asc");
    } else if (one.order === "descend") {
      setSortField(field); setSortOrder("desc");
    } else {
      setSortField(field); setSortOrder(prev => prev === "asc" ? "desc" : "asc");
    }
    setPage(1);
  };

  const activeFilterCount = Object.keys(appliedFilters).length;

  return (
    <div style={{ width: "100%", boxSizing: "border-box" }}>
      <Flex vertical gap={20}>
        <div>
          <Typography.Title level={4} style={{ marginBottom: 8 }}>
            个股列表
          </Typography.Title>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 720 }}>
            查看某一交易日全市场个股行情。点击列头筛选图标（<FilterOutlined />/<SearchOutlined />）可按列筛选，点击列头文字排序。
          </Paragraph>
        </div>

        <Card
          style={{ borderRadius: 16, borderColor: token.colorBorderSecondary }}
          styles={{ header: { borderBottom: `1px solid ${token.colorBorderSecondary}`, background: token.colorBgElevated }, body: { padding: "16px 16px 12px" } }}
          title={
            <Flex vertical gap={8} style={{ width: "100%" }}>
              <Flex wrap="wrap" align="center" gap={12}>
                <Text type="secondary" style={{ fontSize: 13 }}>交易日</Text>
                <DatePicker
                  value={picked}
                  onChange={d => { setPicked(d); setPage(1); }}
                  allowClear={false}
                  style={{ minWidth: 140 }}
                />
                {activeFilterCount > 0 && (
                  <Button
                    size="small"
                    onClick={() => {
                      setAppliedFilters({});
                      setFilterInputs({});
                      setPage(1);
                    }}
                  >
                    清除全部筛选（{activeFilterCount}）
                  </Button>
                )}
              </Flex>
              {tradeDateStr ? (
                <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.5 }}>
                  数据日 {tradeDateStr}
                  {latestBar && latestBar !== tradeDateStr ? ` · 本地最新 bar ${latestBar}` : ""}
                  ，共 {total} 只个股
                </Text>
              ) : null}
            </Flex>
          }
        >
          <div style={{ width: "100%", overflow: "auto" }}>
            <Table<DailyUniverseRow>
              size="small"
              rowKey="ts_code"
              loading={loading}
              columns={columns}
              dataSource={items}
              rowClassName={zebraRowClass}
              sticky={{ offsetHeader: 0 }}
              style={{ minWidth: "100%" }}
              scroll={{ x: "max-content" }}
              pagination={{
                current: page,
                pageSize,
                total,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100, 200],
                showTotal: t => `共 ${t} 条`,
                responsive: true,
                style: { marginBottom: 0 },
              }}
              onChange={onTableChange}
            />
          </div>
        </Card>
      </Flex>
    </div>
  );
}
