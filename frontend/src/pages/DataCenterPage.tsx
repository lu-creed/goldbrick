import {
  Button,
  Card,
  DatePicker,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { type Dayjs } from "dayjs";
import { type Key, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type DataCenterRow,
  type IndexCandidateRow,
  type SymbolDailyRow,
  applyIndexMetaSelection,
  fetchDataCenter,
  fetchIndexCandidates,
  fetchSyncBySelection,
  fetchSymbolDaily,
  getApiErrorMessage,
  syncStockListMeta,
  triggerSingleDaySync,
} from "../api/client";

type SyncingMap = Record<string, boolean>;

/** Tushare exchange 代码 → 中文（文档：SSE/SZSE/BSE） */
const EXCHANGE_LABEL: Record<string, string> = {
  SSE: "上交所",
  SZSE: "深交所",
  BSE: "北交所",
};

const INDEX_MARKET_OPTIONS = [
  { value: "", label: "全部市场" },
  { value: "SSE", label: "上交所" },
  { value: "SZSE", label: "深交所" },
  { value: "CSI", label: "中证指数" },
  { value: "MSCI", label: "MSCI" },
  { value: "SW", label: "申万" },
  { value: "CICC", label: "中金" },
  { value: "OTH", label: "其他" },
];

export default function DataCenterPage() {
  const [searchParams] = useSearchParams();
  /** 「数据池」页内：`?tab=index` 可直达指数登记页签（与数据看板·个股列表无关） */
  const tabParam = searchParams.get("tab");
  const [rows, setRows] = useState<DataCenterRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const [batchSyncing, setBatchSyncing] = useState(false);
  const [syncingRow, setSyncingRow] = useState<SyncingMap>({});
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [activeTab, setActiveTab] = useState<"stock" | "index">(() =>
    tabParam === "index" ? "index" : "stock",
  );
  const [q, setQ] = useState("");
  const [syncedFilter, setSyncedFilter] = useState<"all" | "yes" | "no">("all");
  const [lastSyncDate, setLastSyncDate] = useState<Dayjs | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(12);

  const [indexModalOpen, setIndexModalOpen] = useState(false);
  const [indexLoading, setIndexLoading] = useState(false);
  const [indexCandidates, setIndexCandidates] = useState<IndexCandidateRow[]>([]);
  const [indexMarket, setIndexMarket] = useState<string>("");
  const [indexSearch, setIndexSearch] = useState("");
  const [indexSelectedKeys, setIndexSelectedKeys] = useState<Key[]>([]);
  const [indexSubmitting, setIndexSubmitting] = useState(false);

  const [dailyOpen, setDailyOpen] = useState(false);
  const [dailyCode, setDailyCode] = useState<string | null>(null);
  const [dailyName, setDailyName] = useState<string | null>(null);
  const [dailyRange, setDailyRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [dailyPage, setDailyPage] = useState(1);
  const [dailyPageSize, setDailyPageSize] = useState(20);
  const [dailyLoading, setDailyLoading] = useState(false);
  const [dailyTotal, setDailyTotal] = useState(0);
  const [dailyRows, setDailyRows] = useState<SymbolDailyRow[]>([]);
  const [daySyncing, setDaySyncing] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await fetchDataCenter(5000);
      setRows(data);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (tabParam === "index") setActiveTab("index");
  }, [tabParam]);

  const loadIndexCandidates = useCallback(async () => {
    setIndexLoading(true);
    try {
      const data = await fetchIndexCandidates({
        market: indexMarket || undefined,
        limit: 4000,
      });
      setIndexCandidates(data);
      setIndexSelectedKeys([]);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setIndexLoading(false);
    }
  }, [indexMarket]);

  const loadDaily = useCallback(async () => {
    if (!dailyCode) return;
    setDailyLoading(true);
    try {
      const data = await fetchSymbolDaily({
        ts_code: dailyCode,
        start: dailyRange?.[0]?.format("YYYY-MM-DD"),
        end: dailyRange?.[1]?.format("YYYY-MM-DD"),
        page: dailyPage,
        page_size: dailyPageSize,
      });
      setDailyTotal(data.total);
      setDailyRows(data.items);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setDailyLoading(false);
    }
  }, [dailyCode, dailyRange, dailyPage, dailyPageSize]);

  useEffect(() => {
    if (dailyOpen && dailyCode) void loadDaily();
  }, [dailyOpen, dailyCode, dailyPage, dailyPageSize, dailyRange, loadDaily]);

  const openDaily = (r: DataCenterRow) => {
    if (!r.synced_once) {
      message.warning("该标的暂无 K 线，请先点「同步」");
      return;
    }
    setDailyCode(r.ts_code);
    setDailyName(r.name ?? null);
    setDailyRange(null);
    setDailyPage(1);
    setDailyOpen(true);
  };

  const syncOneDay = async (tradeDate: string) => {
    if (!dailyCode) return;
    setDaySyncing(tradeDate);
    try {
      await triggerSingleDaySync(dailyCode, tradeDate);
      message.success("已触发单日同步，请到「同步任务」查看记录与日志");
      await loadDaily();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setDaySyncing(null);
    }
  };

  const filteredIndexCandidates = useMemo(() => {
    const qq = indexSearch.trim().toLowerCase();
    if (!qq) return indexCandidates;
    return indexCandidates.filter(
      (c) =>
        c.ts_code.toLowerCase().includes(qq) ||
        (c.name || "").toLowerCase().includes(qq) ||
        (c.publisher || "").toLowerCase().includes(qq),
    );
  }, [indexCandidates, indexSearch]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (activeTab === "stock" && r.asset_type !== "stock") return false;
      if (activeTab === "index" && r.asset_type !== "index") return false;
      const qq = q.trim().toLowerCase();
      if (qq) {
        const hitCode = r.ts_code.toLowerCase().includes(qq);
        const hitName = (r.name || "").toLowerCase().includes(qq);
        const hitMarket = (r.market || "").toLowerCase().includes(qq);
        const hitEx = (r.exchange || "").toLowerCase().includes(qq);
        if (!hitCode && !hitName && !hitMarket && !hitEx) return false;
      }
      if (syncedFilter === "yes" && !r.synced_once) return false;
      if (syncedFilter === "no" && r.synced_once) return false;
      if (lastSyncDate) {
        if (!r.last_bar_date) return false;
        if (r.last_bar_date < lastSyncDate.format("YYYY-MM-DD")) return false;
      }
      return true;
    });
  }, [rows, activeTab, q, syncedFilter, lastSyncDate]);

  useEffect(() => {
    setCurrentPage(1);
  }, [q, activeTab, syncedFilter, lastSyncDate]);

  useEffect(() => {
    setSelectedRowKeys([]);
  }, [activeTab]);

  const syncOne = async (row: DataCenterRow) => {
    setSyncingRow((m) => ({ ...m, [row.ts_code]: true }));
    try {
      const today = new Date().toISOString().slice(0, 10);
      await fetchSyncBySelection({
        ts_codes: [row.ts_code],
        end_date: today,
        from_listing: true,
      });
      message.success(`已触发 ${row.ts_code} 同步`);
      await refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setSyncingRow((m) => ({ ...m, [row.ts_code]: false }));
    }
  };

  const syncBatch = async () => {
    const codes = selectedRowKeys.map(String).filter(Boolean);
    if (!codes.length) {
      message.warning("请先勾选至少一个标的");
      return;
    }
    const preview = codes.slice(0, 5).join("、");
    const moreText = codes.length > 5 ? `，等 ${codes.length} 个标的` : "";
    const ok = await new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: "确认批量同步",
        content: `本次将同步 ${codes.length} 个标的（个股与指数将自动走对应接口）：${preview}${moreText}。是否继续？`,
        okText: "确认同步",
        cancelText: "取消",
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
    if (!ok) return;
    setBatchSyncing(true);
    try {
      const today = new Date().toISOString().slice(0, 10);
      await fetchSyncBySelection({
        ts_codes: codes,
        end_date: today,
        from_listing: true,
      });
      message.success(`已触发批量同步（${codes.length} 个标的）`);
      await refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setBatchSyncing(false);
    }
  };

  const adjColumnRender = (_: unknown, r: DataCenterRow) => {
    if (r.asset_type === "index") {
      return <Tag>指数不适用</Tag>;
    }
    if (r.bar_count <= 0) return <Tag>无K线</Tag>;
    if (r.adj_factor_synced) return <Tag color="green">已同步 {r.adj_factor_count}/{r.bar_count}</Tag>;
    if (r.adj_factor_count === 0)
      return (
        <Tooltip title="复权因子未同步，请重新执行同步任务以拉取复权数据">
          <Tag color="red">未同步 0/{r.bar_count}</Tag>
        </Tooltip>
      );
    return (
      <Tooltip title={`已同步 ${r.adj_factor_count} 条，仍有 ${r.bar_count - r.adj_factor_count} 条缺失，建议重新同步`}>
        <Tag color="orange">部分同步 {r.adj_factor_count}/{r.bar_count}</Tag>
      </Tooltip>
    );
  };

  const stockColumns: ColumnsType<DataCenterRow> = [
    { title: "代码", dataIndex: "ts_code", width: 120 },
    { title: "名称", dataIndex: "name", width: 120, ellipsis: true },
    { title: "市场类别", dataIndex: "market", width: 100, ellipsis: true, render: (v: string | null) => v || "-" },
    {
      title: "交易所",
      dataIndex: "exchange",
      width: 90,
      render: (v: string | null) => {
        if (!v) return "-";
        return EXCHANGE_LABEL[v] ?? v;
      },
    },
    { title: "上市日期", dataIndex: "list_date", width: 110, render: (v: string | null) => v || "-" },
    { title: "已全量过", dataIndex: "synced_once", width: 100, render: (v: boolean) => (v ? "是" : "否") },
    { title: "最新同步日期", dataIndex: "last_bar_date", width: 130, render: (v: string | null) => v || "-" },
    { title: "K线条数", dataIndex: "bar_count", width: 90 },
    { title: "复权因子状态", key: "adj_status", width: 200, render: adjColumnRender },
    {
      title: "操作",
      key: "actions",
      width: 200,
      render: (_, r) => (
        <Space size="small" wrap>
          <Button size="small" onClick={() => openDaily(r)}>
            日线明细
          </Button>
          <Button size="small" loading={!!syncingRow[r.ts_code]} onClick={() => void syncOne(r)}>
            同步
          </Button>
        </Space>
      ),
    },
  ];

  const indexColumns: ColumnsType<DataCenterRow> = [
    { title: "代码", dataIndex: "ts_code", width: 120 },
    { title: "名称", dataIndex: "name", width: 160, ellipsis: true },
    { title: "上市日期", dataIndex: "list_date", width: 110, render: (v: string | null) => v || "-" },
    { title: "已全量过", dataIndex: "synced_once", width: 100, render: (v: boolean) => (v ? "是" : "否") },
    { title: "最新同步日期", dataIndex: "last_bar_date", width: 130, render: (v: string | null) => v || "-" },
    { title: "K线条数", dataIndex: "bar_count", width: 90 },
    { title: "复权因子状态", key: "adj_status", width: 140, render: adjColumnRender },
    {
      title: "操作",
      key: "actions",
      width: 200,
      render: (_, r) => (
        <Space size="small" wrap>
          <Button size="small" onClick={() => openDaily(r)}>
            日线明细
          </Button>
          <Button size="small" loading={!!syncingRow[r.ts_code]} onClick={() => void syncOne(r)}>
            同步
          </Button>
        </Space>
      ),
    },
  ];

  const columns = activeTab === "stock" ? stockColumns : indexColumns;

  const dailyColumns: ColumnsType<SymbolDailyRow> = [
    { title: "交易日", dataIndex: "trade_date", width: 110 },
    { title: "开", dataIndex: "open", width: 72, render: (v: number) => v.toFixed(2) },
    { title: "高", dataIndex: "high", width: 72, render: (v: number) => v.toFixed(2) },
    { title: "低", dataIndex: "low", width: 72, render: (v: number) => v.toFixed(2) },
    { title: "收", dataIndex: "close", width: 72, render: (v: number) => v.toFixed(2) },
    { title: "量", dataIndex: "volume", width: 90 },
    { title: "换手%", dataIndex: "turnover_rate", width: 80, render: (v: number | null) => (v != null ? v.toFixed(2) : "-") },
    {
      title: "复权因子",
      dataIndex: "has_adj_factor",
      width: 100,
      render: (v: boolean) => (v ? <Tag color="green">有</Tag> : <Tag color="red">无</Tag>),
    },
    {
      title: "单日同步",
      key: "d",
      width: 100,
      render: (_, row) => (
        <Button size="small" loading={daySyncing === row.trade_date} onClick={() => void syncOneDay(row.trade_date)}>
          同步
        </Button>
      ),
    },
  ];

  const toggleSelected = (code: string) => {
    setSelectedRowKeys((prev) => {
      const exists = prev.includes(code);
      if (exists) return prev.filter((k) => k !== code);
      return [...prev, code];
    });
  };

  const submitIndexSelection = async () => {
    if (!indexSelectedKeys.length) {
      message.warning("请至少勾选一条指数");
      return;
    }
    const pick = new Set(indexSelectedKeys.map(String));
    const items = filteredIndexCandidates
      .filter((c) => pick.has(c.ts_code))
      .map((c) => ({
        ts_code: c.ts_code,
        name: c.name ?? undefined,
        list_date: c.list_date ?? undefined,
      }));
    setIndexSubmitting(true);
    try {
      const ret = await applyIndexMetaSelection(items);
      message.success(`已加入数据后台：新增 ${ret.added}，已存在跳过 ${ret.skipped}`);
      setIndexModalOpen(false);
      await refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setIndexSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        数据池
      </Typography.Title>
      <Card
        extra={
          <Space wrap>
            <Button
              loading={syncingAll}
              onClick={async () => {
                setSyncingAll(true);
                try {
                  const ret = await syncStockListMeta();
                  const ins = ret.inserted_stocks ?? 0;
                  const upd = ret.updated_stocks ?? 0;
                  message.success(`股票列表已更新：新增 ${ins} 只，信息更新 ${upd} 只；当前个股 ${ret.stock_count}、指数 ${ret.index_count}`);
                  await refresh();
                } catch (e) {
                  message.error(getApiErrorMessage(e));
                } finally {
                  setSyncingAll(false);
                }
              }}
            >
              更新股票列表
            </Button>
            <Button
              type="primary"
              onClick={() => {
                setIndexModalOpen(true);
                setIndexSearch("");
                void loadIndexCandidates();
              }}
            >
              更新指数列表
            </Button>
          </Space>
        }
      >
        <Tabs
          activeKey={activeTab}
          onChange={(k) => setActiveTab(k as "stock" | "index")}
          items={[
            { key: "stock", label: "个股" },
            { key: "index", label: "指数" },
          ]}
          style={{ marginBottom: 12 }}
        />
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            allowClear
            style={{ width: 220 }}
            placeholder="搜索代码/名称/市场/交易所"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Select
            style={{ width: 160 }}
            value={syncedFilter}
            onChange={(v) => setSyncedFilter(v)}
            options={[
              { value: "all", label: "全部同步状态" },
              { value: "yes", label: "已全量同步" },
              { value: "no", label: "未全量同步" },
            ]}
          />
          <DatePicker
            value={lastSyncDate}
            onChange={(d) => setLastSyncDate(d)}
            placeholder="最新同步日期>=..."
            allowClear
          />
          <Button
            onClick={() => {
              setQ("");
              setSyncedFilter("all");
              setLastSyncDate(null);
            }}
          >
            清空筛选
          </Button>
          <Button onClick={() => setSelectedRowKeys([])} disabled={!selectedRowKeys.length}>
            清空勾选
          </Button>
          <Button type="primary" loading={batchSyncing} onClick={() => void syncBatch()}>
            批量同步已勾选（{selectedRowKeys.length}）
          </Button>
        </Space>
        <Table
          rowKey="ts_code"
          size="small"
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys),
            preserveSelectedRowKeys: true,
          }}
          onRow={(record) => ({
            onClick: (event) => {
              const target = event.target as HTMLElement;
              if (target.closest("button,a,.ant-checkbox-wrapper,.ant-checkbox-input")) return;
              toggleSelected(record.ts_code);
            },
          })}
          columns={columns}
          dataSource={filtered}
          pagination={{
            current: currentPage,
            pageSize,
            showSizeChanger: true,
            pageSizeOptions: ["12", "24", "48", "96"],
            onChange: (page, size) => {
              setCurrentPage(page);
              if (size && size !== pageSize) {
                setPageSize(size);
              }
            },
          }}
          scroll={{ x: 1100 }}
        />
      </Card>

      <Modal
        title="从 Tushare 选择指数加入数据后台"
        open={indexModalOpen}
        onCancel={() => setIndexModalOpen(false)}
        width={900}
        okText="加入数据后台"
        confirmLoading={indexSubmitting}
        onOk={() => void submitIndexSelection()}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" className="!mb-3 text-sm">
          勾选后写入元数据与同步池；已加入的指数不会重复写入。本版本不支持从后台移除指数。
        </Typography.Paragraph>
        <Space wrap className="mb-3">
          <Select
            style={{ width: 160 }}
            value={indexMarket}
            onChange={(v) => setIndexMarket(v)}
            options={INDEX_MARKET_OPTIONS}
          />
          <Button loading={indexLoading} onClick={() => void loadIndexCandidates()}>
            重新加载
          </Button>
          <Input
            allowClear
            style={{ width: 220 }}
            placeholder="筛选代码/名称/发布方"
            value={indexSearch}
            onChange={(e) => setIndexSearch(e.target.value)}
          />
        </Space>
        <Table<IndexCandidateRow>
          rowKey="ts_code"
          size="small"
          loading={indexLoading}
          rowSelection={{
            selectedRowKeys: indexSelectedKeys,
            onChange: (keys) => setIndexSelectedKeys(keys),
          }}
          columns={[
            { title: "代码", dataIndex: "ts_code", width: 120 },
            { title: "名称", dataIndex: "name", ellipsis: true },
            { title: "市场", dataIndex: "market", width: 90 },
            { title: "发布方", dataIndex: "publisher", width: 100, ellipsis: true },
            { title: "发布日期", dataIndex: "list_date", width: 110 },
          ]}
          dataSource={filteredIndexCandidates}
          pagination={{ pageSize: 12, showSizeChanger: true }}
          scroll={{ y: 360 }}
        />
      </Modal>

      <Modal
        title={dailyCode ? `日线明细 · ${dailyCode}${dailyName ? ` ${dailyName}` : ""}` : "日线明细"}
        open={dailyOpen}
        onCancel={() => setDailyOpen(false)}
        width={960}
        footer={null}
        destroyOnClose
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <DatePicker.RangePicker
            value={dailyRange ?? undefined}
            onChange={(r) => {
              setDailyRange(r as [Dayjs, Dayjs] | null);
              setDailyPage(1);
            }}
          />
          <Button onClick={() => void loadDaily()} loading={dailyLoading}>
            刷新
          </Button>
        </Space>
        <Table<SymbolDailyRow>
          rowKey="trade_date"
          size="small"
          loading={dailyLoading}
          columns={dailyColumns}
          dataSource={dailyRows}
          pagination={{
            current: dailyPage,
            pageSize: dailyPageSize,
            total: dailyTotal,
            showSizeChanger: true,
            pageSizeOptions: ["10", "20", "50", "100"],
            onChange: (p, ps) => {
              setDailyPage(p);
              if (ps) setDailyPageSize(ps);
            },
          }}
          scroll={{ x: 860 }}
        />
      </Modal>
    </Space>
  );
}
