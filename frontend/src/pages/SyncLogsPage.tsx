import {
  Alert,
  Button,
  Card,
  Modal,
  Popconfirm,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import {
  type SyncRun,
  cancelSyncRun,
  deleteSyncRun,
  fetchSyncRunLog,
  fetchSyncRuns,
  getApiErrorMessage,
  pauseSyncRun,
  resumeSyncRun,
} from "../api/client";
import { zebraRowClass } from "../constants/theme";

export default function SyncLogsPage() {
  const [runs, setRuns] = useState<SyncRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [runActionId, setRunActionId] = useState<number | null>(null);
  const [logModal, setLogModal] = useState<{ open: boolean; content: string; runId: number | null }>({
    open: false,
    content: "",
    runId: null,
  });
  const [logLoading, setLogLoading] = useState(false);

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    if (!silent) setLoading(true);
    try {
      const r = await fetchSyncRuns(50);
      setRuns(r);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

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

  const onForceCancelRun = async (id: number) => {
    setRunActionId(id);
    try {
      await cancelSyncRun(id, { force: true });
      message.success("已强制记为已取消");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
    }
  };

  const onDeleteRun = async (id: number) => {
    setRunActionId(id);
    try {
      await deleteSyncRun(id);
      message.success("已删除");
      void refresh({ silent: true });
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setRunActionId(null);
    }
  };

  const onOpenLog = async (runId: number) => {
    setLogModal({ open: true, content: "", runId });
    setLogLoading(true);
    try {
      const text = await fetchSyncRunLog(runId);
      setLogModal({ open: true, content: text, runId });
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setLogModal({ open: false, content: "", runId: null });
    } finally {
      setLogLoading(false);
    }
  };

  const columns: ColumnsType<SyncRun> = [
    { title: "ID", dataIndex: "id", width: 64 },
    { title: "开始", dataIndex: "started_at", width: 180 },
    { title: "结束", dataIndex: "finished_at", width: 180 },
    { title: "触发", dataIndex: "trigger", width: 100 },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (status: string) => {
        const colorMap: Record<string, string> = {
          queued: "default",
          running: "processing",
          paused: "warning",
          success: "success",
          failed: "error",
          cancelled: "default",
        };
        return <Tag color={colorMap[status] ?? "default"}>{status}</Tag>;
      },
    },
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
        const terminal = record.status === "cancelled" || record.status === "failed";
        const isActing = runActionId === record.id;

        if (terminal) {
          return (
            <Popconfirm
              title="确定删除此条记录？"
              description="删除后不可恢复。"
              onConfirm={() => void onDeleteRun(record.id)}
            >
              <Button size="small" danger loading={isActing}>
                删除
              </Button>
            </Popconfirm>
          );
        }

        if (!active) return "—";

        const showPause =
          (record.status === "queued" || record.status === "running") && !record.pause_requested;
        const showResume = Boolean(record.pause_requested) || record.status === "paused";
        return (
          <Space size="small" wrap>
            {showPause && (
              <Button size="small" loading={isActing} onClick={() => void onPauseRun(record.id)}>
                暂停
              </Button>
            )}
            {showResume && (
              <Button size="small" loading={isActing} onClick={() => void onResumeRun(record.id)}>
                继续
              </Button>
            )}
            <Popconfirm title="确定取消本次同步？" onConfirm={() => void onCancelRun(record.id)}>
              <Button size="small" danger loading={isActing}>
                取消
              </Button>
            </Popconfirm>
            <Popconfirm
              title="强制结束本条运行？"
              description="仅当多次点「取消」仍显示 running 时用：直接把状态改为已取消。若仍有工作线程，其会尽快退出。"
              onConfirm={() => void onForceCancelRun(record.id)}
            >
              <Button size="small" danger type="dashed" loading={isActing}>
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
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => void onOpenLog(record.id)}>
            打开日志
          </Button>
        ) : (
          "-"
        ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        同步日志
      </Typography.Title>
      <Card
        title="运行记录"
        loading={loading}
        extra={
          <Button size="small" onClick={() => void refresh()}>
            刷新
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="协作式取消：在上一只标的整段处理结束后才会停；该段期间仍为 running。若多次「取消」仍不变，请用「强制结束」。已取消/失败的记录可点「删除」移除。"
        />
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={runs}
          rowClassName={zebraRowClass}
          pagination={{ pageSize: 15 }}
        />
      </Card>
      <Modal
        title={`同步日志 #${logModal.runId}`}
        open={logModal.open}
        onCancel={() => setLogModal({ open: false, content: "", runId: null })}
        footer={null}
        width={900}
        styles={{ body: { padding: 0 } }}
      >
        {logLoading ? (
          <div style={{ padding: 24, textAlign: "center" }}>加载中…</div>
        ) : (
          <pre
            style={{
              margin: 0,
              padding: 16,
              maxHeight: 600,
              overflow: "auto",
              fontSize: 12,
              lineHeight: 1.6,
              background: "#1e1e1e",
              color: "#d4d4d4",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {logModal.content || "（日志为空）"}
          </pre>
        )}
      </Modal>
    </Space>
  );
}
