import {
  Alert,
  Button,
  Card,
  Descriptions,
  InputNumber,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import {
  AutoUpdateLog,
  AutoUpdateStatus,
  fetchAutoUpdateStatus,
  getApiErrorMessage,
  triggerAutoUpdateNow,
  updateAutoUpdateConfig,
} from "../api/client";

const { Title, Text } = Typography;

type CurrentUser = { id: number; username: string; is_admin: boolean };
type Props = { currentUser: CurrentUser };

/** 每 10 秒自动刷新一次状态 */
const POLL_INTERVAL_MS = 10_000;

/** 把 ISO 时间字符串转成本地可读形式（YYYY-MM-DD HH:mm:ss），空值显示 "-" */
function formatTime(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** 状态标签配色：ok 绿 / no-change 灰 / error 红 */
function statusTag(status: string) {
  if (status === "ok") return <Tag color="success">成功</Tag>;
  if (status === "no-change") return <Tag color="default">无变更</Tag>;
  if (status === "error") return <Tag color="error">失败</Tag>;
  return <Tag>{status}</Tag>;
}

function actionLabel(action: string) {
  if (action === "check") return "检查";
  if (action === "deploy") return "部署";
  return action;
}

export default function AutoUpdatePage({ currentUser }: Props) {
  if (!currentUser.is_admin) return <Navigate to="/" replace />;

  const [status, setStatus] = useState<AutoUpdateStatus | null>(null);
  const [loading, setLoading] = useState(false);

  // 配置表单的草稿值（编辑中还没保存）
  const [draftEnabled, setDraftEnabled] = useState(false);
  const [draftInterval, setDraftInterval] = useState<number>(5);
  const [saving, setSaving] = useState(false);
  const [triggering, setTriggering] = useState(false);

  // 用 ref 存 timer 句柄，卸载时清理；避免多次 setInterval 叠加
  const pollTimerRef = useRef<number | null>(null);

  async function loadStatus(opts?: { silent?: boolean }) {
    if (!opts?.silent) setLoading(true);
    try {
      const data = await fetchAutoUpdateStatus();
      setStatus(data);
      // 首次加载时同步草稿值；之后不再覆盖用户正在编辑的字段
      setDraftEnabled((prev) => (status ? prev : data.config.enabled));
      setDraftInterval((prev) => (status ? prev : data.config.interval_minutes));
    } catch (err) {
      if (!opts?.silent) message.error(getApiErrorMessage(err));
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }

  // 首次加载 + 每 10 秒静默刷新
  useEffect(() => {
    loadStatus();
    pollTimerRef.current = window.setInterval(() => loadStatus({ silent: true }), POLL_INTERVAL_MS);
    return () => {
      if (pollTimerRef.current != null) window.clearInterval(pollTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSave() {
    if (!status) return;
    setSaving(true);
    try {
      const updated = await updateAutoUpdateConfig({
        enabled: draftEnabled,
        interval_minutes: draftInterval,
      });
      setStatus((prev) => (prev ? { ...prev, config: { ...prev.config, ...updated } } : prev));
      message.success("配置已保存");
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleTriggerNow() {
    setTriggering(true);
    try {
      const res = await triggerAutoUpdateNow();
      message.success(res.message || "已触发");
      // 立即刷新一次，几秒后再刷一次（给后台线程跑完的时间）
      await loadStatus({ silent: true });
      setTimeout(() => loadStatus({ silent: true }), 4000);
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setTriggering(false);
    }
  }

  const hasUnsavedChanges =
    status &&
    (draftEnabled !== status.config.enabled || draftInterval !== status.config.interval_minutes);

  const columns: ColumnsType<AutoUpdateLog> = [
    {
      title: "时间",
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => <Text style={{ fontFamily: "monospace", fontSize: 12 }}>{formatTime(v)}</Text>,
    },
    {
      title: "动作",
      dataIndex: "action",
      width: 90,
      render: (v: string) => actionLabel(v),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => statusTag(v),
    },
    {
      title: "详情",
      dataIndex: "details",
      render: (v: string | null) => (
        <Text style={{ fontSize: 12, wordBreak: "break-all" }}>{v || "-"}</Text>
      ),
    },
    {
      title: "耗时",
      dataIndex: "duration_ms",
      width: 90,
      render: (v: number | null) => (v != null ? `${v} ms` : "-"),
    },
  ];

  return (
    <div style={{ padding: "0 4px" }}>
      <Title level={4} style={{ marginTop: 0, marginBottom: 16 }}>GitHub 自动更新</Title>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="工作原理"
        description={
          <div style={{ fontSize: 13, lineHeight: 1.7 }}>
            启用后，服务器每隔 <b>N 分钟</b>自动检查 GitHub 上 <code>origin/main</code> 有没有新 commit。
            发现新提交 → 自动 <code>git pull</code> → 重新构建前端 → <code>pm2 restart</code> 后端。
            全过程无需人工干预，日志见下方表格。本页每 10 秒自动刷新。
          </div>
        }
      />

      {/* 配置卡片 */}
      <Card
        size="small"
        title="配置"
        loading={loading && !status}
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            <Button onClick={() => loadStatus()} loading={loading}>刷新</Button>
            <Button onClick={handleTriggerNow} loading={triggering} disabled={!status}>
              立即检查一次
            </Button>
          </Space>
        }
      >
        {status && (
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" bordered>
              <Descriptions.Item label="上次检查时间">
                {formatTime(status.config.last_run_at)}
              </Descriptions.Item>
              <Descriptions.Item label="上次远程 commit">
                {status.config.last_commit_hash
                  ? <Text style={{ fontFamily: "monospace" }}>{status.config.last_commit_hash.slice(0, 12)}</Text>
                  : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="当前状态">
                {status.config.enabled
                  ? <Tag color="success">已启用</Tag>
                  : <Tag color="default">已停用</Tag>}
              </Descriptions.Item>
            </Descriptions>

            <Space wrap>
              <Space>
                <Text>启用自动更新：</Text>
                <Switch checked={draftEnabled} onChange={setDraftEnabled} />
              </Space>
              <Space>
                <Text>检查频率：每</Text>
                <InputNumber
                  min={1}
                  max={1440}
                  value={draftInterval}
                  onChange={(v) => typeof v === "number" && setDraftInterval(v)}
                  style={{ width: 100 }}
                />
                <Text>分钟</Text>
              </Space>
              <Button
                type="primary"
                onClick={handleSave}
                loading={saving}
                disabled={!hasUnsavedChanges}
              >
                保存配置
              </Button>
              {hasUnsavedChanges && <Text type="warning">有未保存修改</Text>}
            </Space>
          </Space>
        )}
      </Card>

      {/* 日志卡片 */}
      <Card size="small" title={`执行日志（最近 ${status?.recent_logs.length ?? 0} 条）`}>
        <Table<AutoUpdateLog>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={status?.recent_logs ?? []}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: "暂无日志（启用后等一个周期，或点击「立即检查一次」）" }}
        />
      </Card>
    </div>
  );
}
