/**
 * 自选股池页面
 *
 * 功能：集中展示用户在各页面（复盘、选股、回测等）收藏的股票。
 * - 点击股票代码跳转 K 线页
 * - 可从此页面移除不再关注的股票
 * - 可在此页面手动添加股票（按代码搜索）
 *
 * 与「大V看板」的区别：
 * - 大V看板：需要填写派息率/EPS 的精细管理清单
 * - 自选股池：轻量收藏，快速标记感兴趣但尚未深研的股票
 */
import { DeleteOutlined, PlusOutlined, StarFilled } from "@ant-design/icons";
import {
  Button,
  Card,
  Input,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  WatchlistItem,
  addToWatchlist,
  fetchWatchlist,
  getApiErrorMessage,
  removeFromWatchlist,
} from "../api/client";
import { RISE_COLOR, zebraRowClass } from "../constants/theme";

const { Text } = Typography;

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(false);

  // ── 手动添加状态 ─────────────────────────────────────────
  // 简单的「输入代码」方式添加，不做复杂搜索
  const [addCode, setAddCode] = useState("");
  const [adding, setAdding] = useState(false);

  // ── 数据加载 ─────────────────────────────────────────────

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await fetchWatchlist());
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // ── 操作 ─────────────────────────────────────────────────

  /** 移除一只股票并刷新列表 */
  const handleRemove = useCallback(async (ts_code: string) => {
    try {
      await removeFromWatchlist(ts_code);
      message.success(`已移除 ${ts_code}`);
      void load();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    }
  }, [load]);

  /** 手动输入代码添加到自选股池 */
  const handleAdd = useCallback(async () => {
    const code = addCode.trim().toUpperCase();
    if (!code) return;
    setAdding(true);
    try {
      await addToWatchlist(code, null, null);
      message.success(`已添加 ${code}`);
      setAddCode("");
      void load();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setAdding(false);
    }
  }, [addCode, load]);

  // ── 表格列定义 ────────────────────────────────────────────

  const columns: ColumnsType<WatchlistItem> = [
    {
      title: "代码",
      dataIndex: "ts_code",
      width: 120,
      render: (v: string) => (
        // 点击代码跳转 K 线页，方便快速查看走势
        <Link to={`/?ts_code=${encodeURIComponent(v)}`}>{v}</Link>
      ),
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 120,
      render: (v: string | null) => v ?? <Text type="secondary">—</Text>,
    },
    {
      title: "备注",
      dataIndex: "note",
      ellipsis: true,
      render: (v: string | null) =>
        v ? <Text type="secondary">{v}</Text> : null,
    },
    {
      title: "加入时间",
      dataIndex: "created_at",
      width: 140,
      render: (v: string) => dayjs(v).format("MM-DD HH:mm"),
    },
    {
      title: "操作",
      key: "actions",
      width: 80,
      render: (_: unknown, row: WatchlistItem) => (
        <Popconfirm
          title={`移除 ${row.ts_code}？`}
          okText="移除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onConfirm={() => void handleRemove(row.ts_code)}
        >
          <Button size="small" type="link" danger icon={<DeleteOutlined />}>
            移除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 900 }}>
      {/* 页头 */}
      <div>
        <Typography.Title level={4} style={{ margin: 0 }}>
          <StarFilled style={{ color: RISE_COLOR, marginRight: 8 }} />
          自选股池
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
          在复盘、选股等页面点击 ⭐ 即可收藏。点击代码直接跳转 K 线页查看走势。
        </Typography.Paragraph>
      </div>

      {/* 手动添加输入栏 */}
      <Card size="small">
        <Space>
          <Input
            placeholder="输入股票代码，如 000001.SZ"
            value={addCode}
            onChange={(e) => setAddCode(e.target.value)}
            onPressEnter={() => void handleAdd()}
            style={{ width: 240 }}
            allowClear
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={adding}
            onClick={() => void handleAdd()}
          >
            手动添加
          </Button>
          <Tag color="default" style={{ fontSize: 12 }}>
            共 {items.length} 只
          </Tag>
        </Space>
      </Card>

      {/* 自选股列表 */}
      <Card>
        <Table<WatchlistItem>
          rowKey="ts_code"
          size="small"
          columns={columns}
          dataSource={items}
          loading={loading}
          rowClassName={zebraRowClass}
          pagination={{
            pageSize: 50,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 只`,
          }}
          locale={{ emptyText: "暂无自选股，在复盘或选股页面点击 ⭐ 添加" }}
        />
      </Card>
    </Space>
  );
}
