/**
 * StrategyDrawer:策略列表 + 详情 + Markdown 笔记的统一抽屉。
 *
 * 被 BacktestPage 和 ScreeningPage 共用(通过 kind prop 区分 screen / backtest)。
 *
 * 交互:
 *   1. 打开抽屉自动拉本用户所有策略(当前 kind)
 *   2. 左侧列表选中 → 右侧展示:策略名(只读)、条件逻辑(只读 JSON)、笔记(编辑/预览切换)
 *   3. 点「加载到当前表单」→ 通过 onLoad 回调告诉父组件,同时关闭抽屉
 *   4. 点「保存笔记」→ PATCH /api/strategies/{id} 只更新 notes
 *   5. 点「删除」→ 确认后 DELETE(系统预置策略不显示删除按钮)
 *
 * 设计决策:
 *   - 不在这里做「保存策略」(新建);那个动作发生在父页面点「保存为策略」按钮时
 *   - 系统预置策略 notes 永远为 null,界面上禁用笔记编辑
 *   - Markdown 预览用 react-markdown + remark-gfm(支持表格/任务列表/删除线)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Drawer,
  Empty,
  Input,
  List,
  Popconfirm,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  type StrategyItem,
  type StrategyListItemShort,
  deleteStrategy,
  fetchStrategies,
  getApiErrorMessage,
  getStrategy,
  updateStrategy,
} from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
  kind: "screen" | "backtest";
  onLoad: (strategy: StrategyItem) => void;
}

type NotesMode = "edit" | "preview" | "split";

export default function StrategyDrawer({ open, onClose, kind, onLoad }: Props) {
  const [list, setList] = useState<StrategyListItemShort[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<StrategyItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 笔记编辑状态:本地草稿 + 编辑/预览模式
  const [notesDraft, setNotesDraft] = useState("");
  const [notesDirty, setNotesDirty] = useState(false);
  const [notesMode, setNotesMode] = useState<NotesMode>("edit");
  const [saving, setSaving] = useState(false);

  // 打开或 kind 变更时重新拉列表
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await fetchStrategies(kind);
      setList(rows);
      if (rows.length === 0) {
        setSelectedId(null);
        setDetail(null);
      } else if (!rows.some((r) => r.id === selectedId)) {
        // 当前选中的不在新列表里(如被删),自动选第一条
        setSelectedId(rows[0].id);
      }
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [kind, selectedId]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  // 选中变化时拉详情
  useEffect(() => {
    if (selectedId == null || !open) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const d = await getStrategy(selectedId);
        if (!cancelled) {
          setDetail(d);
          setNotesDraft(d.notes ?? "");
          setNotesDirty(false);
        }
      } catch (e) {
        if (!cancelled) message.error(getApiErrorMessage(e));
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedId, open]);

  // 未保存草稿时提醒切换会丢失
  const handleSelect = (id: number) => {
    if (notesDirty) {
      const ok = window.confirm("当前笔记未保存,切换会丢失修改,确定吗?");
      if (!ok) return;
    }
    setSelectedId(id);
  };

  const handleSaveNotes = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      const updated = await updateStrategy(detail.id, { notes: notesDraft });
      setDetail(updated);
      setNotesDirty(false);
      message.success("笔记已保存");
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    try {
      await deleteStrategy(detail.id);
      message.success(`已删除策略「${detail.display_name}」`);
      setSelectedId(null);
      setDetail(null);
      await refresh();
    } catch (e) {
      message.error(getApiErrorMessage(e));
    }
  };

  const handleLoad = () => {
    if (!detail) return;
    if (notesDirty) {
      const ok = window.confirm("笔记未保存,加载后会丢失修改,仍加载吗?");
      if (!ok) return;
    }
    onLoad(detail);
    onClose();
  };

  // 策略逻辑的 JSON 字符串展示(可读性优先,不做 tree 视图)
  const logicPreview = useMemo(() => {
    if (!detail) return "";
    const payload = detail.kind === "screen"
      ? detail.logic
      : { buy_logic: detail.buy_logic, sell_logic: detail.sell_logic };
    return JSON.stringify(payload, null, 2);
  }, [detail]);

  const isReadOnly = detail?.is_system === true;

  return (
    <Drawer
      title={kind === "screen" ? "我的选股策略" : "我的回测策略"}
      open={open}
      onClose={onClose}
      width={960}
      destroyOnClose
    >
      <div style={{ display: "flex", gap: 12, height: "100%" }}>
        {/* 左侧策略列表 */}
        <div style={{ width: 280, borderRight: "1px solid #f0f0f0", paddingRight: 12, overflowY: "auto" }}>
          <List
            size="small"
            loading={loading}
            dataSource={list}
            locale={{ emptyText: <Empty description="暂无策略" /> }}
            renderItem={(item) => (
              <List.Item
                onClick={() => handleSelect(item.id)}
                style={{
                  cursor: "pointer",
                  padding: "8px 10px",
                  background: selectedId === item.id ? "#1677ff" : undefined,
                  borderRadius: 4,
                  color: selectedId === item.id ? "#fff" : undefined,
                }}
              >
                <div style={{ width: "100%" }}>
                  <Space size={4} wrap>
                    <Typography.Text
                      strong
                      style={{ color: selectedId === item.id ? "#fff" : undefined }}
                    >
                      {item.display_name}
                    </Typography.Text>
                    {item.is_system && <Tag color="default">预置</Tag>}
                  </Space>
                  {item.description && (
                    <Typography.Paragraph
                      ellipsis={{ rows: 2 }}
                      style={{
                        margin: "2px 0 0 0",
                        fontSize: 12,
                        color: selectedId === item.id ? "rgba(255,255,255,0.75)" : undefined,
                      }}
                    >
                      {item.description}
                    </Typography.Paragraph>
                  )}
                </div>
              </List.Item>
            )}
          />
        </div>

        {/* 右侧详情 */}
        <div style={{ flex: 1, overflowY: "auto", paddingLeft: 4 }}>
          {detail ? (
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <div>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {detail.display_name}
                  {isReadOnly && <Tag style={{ marginLeft: 8 }}>系统预置</Tag>}
                </Typography.Title>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {detail.code} · 更新于 {new Date(detail.updated_at).toLocaleString("zh-CN")}
                </Typography.Text>
              </div>

              {detail.description && (
                <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                  {detail.description}
                </Typography.Paragraph>
              )}

              {/* 策略逻辑(只读) */}
              <div>
                <Typography.Text strong>策略逻辑</Typography.Text>
                <pre
                  style={{
                    background: "#f0f0f0",
                    border: "1px solid #d9d9d9",
                    color: "#262626",
                    padding: 8,
                    borderRadius: 4,
                    fontSize: 11,
                    maxHeight: 180,
                    overflow: "auto",
                    marginTop: 4,
                  }}
                >
                  {logicPreview}
                </pre>
              </div>

              {/* 笔记区:编辑/预览/并排 */}
              {!isReadOnly && (
                <div>
                  <Space style={{ marginBottom: 8 }}>
                    <Typography.Text strong>研究笔记(Markdown)</Typography.Text>
                    <Segmented<NotesMode>
                      size="small"
                      options={[
                        { label: "编辑", value: "edit" },
                        { label: "预览", value: "preview" },
                        { label: "并排", value: "split" },
                      ]}
                      value={notesMode}
                      onChange={setNotesMode}
                    />
                    <Button
                      size="small"
                      type="primary"
                      loading={saving}
                      disabled={!notesDirty}
                      onClick={handleSaveNotes}
                    >
                      保存笔记
                    </Button>
                  </Space>

                  <div style={{ display: "flex", gap: 8 }}>
                    {(notesMode === "edit" || notesMode === "split") && (
                      <Input.TextArea
                        value={notesDraft}
                        onChange={(e) => {
                          setNotesDraft(e.target.value);
                          setNotesDirty(true);
                        }}
                        placeholder="# 我的思路&#10;- 阈值选 30 的依据&#10;- 本次调参后观察到..."
                        rows={14}
                        style={{ flex: 1, fontFamily: "monospace" }}
                      />
                    )}
                    {(notesMode === "preview" || notesMode === "split") && (
                      <div
                        style={{
                          flex: 1,
                          minHeight: 280,
                          padding: 12,
                          background: "#fafafa",
                          borderRadius: 4,
                          border: "1px solid #f0f0f0",
                          fontSize: 14,
                          lineHeight: 1.6,
                          overflowY: "auto",
                        }}
                      >
                        {notesDraft.trim() ? (
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{notesDraft}</ReactMarkdown>
                        ) : (
                          <Typography.Text type="secondary">(笔记为空)</Typography.Text>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 底部操作栏 */}
              <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid #f0f0f0" }}>
                <Button type="primary" onClick={handleLoad} loading={detailLoading}>
                  加载到当前表单
                </Button>
                {!isReadOnly && (
                  <Popconfirm title="确定删除此策略?" onConfirm={handleDelete} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                    <Button danger>删除策略</Button>
                  </Popconfirm>
                )}
              </div>
            </Space>
          ) : (
            <Empty description={list.length > 0 ? "请从左侧选择一个策略" : "先从回测或选股页保存一个策略,再来这里管理"} style={{ marginTop: 80 }} />
          )}
        </div>
      </div>
    </Drawer>
  );
}
