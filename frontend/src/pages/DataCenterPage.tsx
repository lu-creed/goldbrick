import { Button, Card, DatePicker, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { type Dayjs } from "dayjs";
import { type Key, useEffect, useMemo, useState } from "react";
import { type DataCenterRow, fetchDataCenter, fetchSyncBySelection, getApiErrorMessage, seedIndicators, syncUniverseMeta } from "../api/client";

type SyncingMap = Record<string, boolean>;

export default function DataCenterPage() {
  const [rows, setRows] = useState<DataCenterRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const [seedingIndicators, setSeedingIndicators] = useState(false);
  const [batchSyncing, setBatchSyncing] = useState(false);
  const [syncingRow, setSyncingRow] = useState<SyncingMap>({});
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [q, setQ] = useState("");
  const [assetType, setAssetType] = useState<"all" | "stock" | "index">("all");
  const [syncedFilter, setSyncedFilter] = useState<"all" | "yes" | "no">("all");
  const [lastSyncDate, setLastSyncDate] = useState<Dayjs | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(12);

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

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      const qq = q.trim().toLowerCase();
      if (qq) {
        const hitCode = r.ts_code.toLowerCase().includes(qq);
        const hitName = (r.name || "").toLowerCase().includes(qq);
        if (!hitCode && !hitName) return false;
      }
      if (assetType !== "all" && r.asset_type !== assetType) return false;
      if (syncedFilter === "yes" && !r.synced_once) return false;
      if (syncedFilter === "no" && r.synced_once) return false;
      if (lastSyncDate) {
        if (!r.last_bar_date) return false;
        if (r.last_bar_date < lastSyncDate.format("YYYY-MM-DD")) return false;
      }
      return true;
    });
  }, [rows, q, assetType, syncedFilter, lastSyncDate]);

  useEffect(() => {
    // 筛选条件变更后回到第一页，避免当前页码超出筛选结果范围。
    setCurrentPage(1);
  }, [q, assetType, syncedFilter, lastSyncDate]);

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
        content: `本次将同步 ${codes.length} 个标的：${preview}${moreText}。是否继续？`,
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

  const columns: ColumnsType<DataCenterRow> = [
    { title: "代码", dataIndex: "ts_code", width: 120 },
    { title: "名称", dataIndex: "name", width: 120, ellipsis: true },
    {
      title: "类型",
      dataIndex: "asset_type",
      width: 90,
      render: (v: string) => (v === "index" ? <Tag color="blue">index</Tag> : <Tag color="green">stock</Tag>),
    },
    { title: "上市日期", dataIndex: "list_date", width: 110, render: (v: string | null) => v || "-" },
    { title: "已全量过", dataIndex: "synced_once", width: 100, render: (v: boolean) => (v ? "是" : "否") },
    { title: "最新同步日期", dataIndex: "last_bar_date", width: 130, render: (v: string | null) => v || "-" },
    { title: "K线条数", dataIndex: "bar_count", width: 90 },
    {
      title: "复权因子状态",
      key: "adj_status",
      width: 200,
      render: (_, r) => {
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
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_, r) => (
        <Button size="small" loading={!!syncingRow[r.ts_code]} onClick={() => void syncOne(r)}>
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        数据后台
      </Typography.Title>
      <Card
        extra={
          <Space>
            <Button
              loading={seedingIndicators}
              onClick={async () => {
                setSeedingIndicators(true);
                try {
                  const ret = await seedIndicators();
                  message.success(ret.message);
                } catch (e) {
                  message.error(getApiErrorMessage(e));
                } finally {
                  setSeedingIndicators(false);
                }
              }}
            >
              初始化指标库
            </Button>
            <Button
              loading={syncingAll}
              onClick={async () => {
                setSyncingAll(true);
                try {
                  const ret = await syncUniverseMeta();
                  if (ret.from_cache) message.info("已使用本地缓存");
                  else message.success(`已同步：个股 ${ret.stock_count}，指数 ${ret.index_count}`);
                  await refresh();
                } catch (e) {
                  message.error(getApiErrorMessage(e));
                } finally {
                  setSyncingAll(false);
                }
              }}
            >
              同步全量标的元数据
            </Button>
          </Space>
        }
      >
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            allowClear
            style={{ width: 220 }}
            placeholder="搜索代码/名称"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Select
            style={{ width: 140 }}
            value={assetType}
            onChange={(v) => setAssetType(v)}
            options={[
              { value: "all", label: "全部类型" },
              { value: "stock", label: "个股" },
              { value: "index", label: "指数" },
            ]}
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
              setAssetType("all");
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
              // 点击按钮/链接等交互控件时，不触发行勾选切换。
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
          scroll={{ x: 1000 }}
        />
      </Card>
    </Space>
  );
}
