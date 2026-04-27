import {
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import {
  UserInfo,
  createUser,
  fetchRegistrationSetting,
  fetchUsers,
  getApiErrorMessage,
  toggleRegistration,
  updateUser,
} from "../api/client";

const { Title, Text } = Typography;

type CurrentUser = { id: number; username: string; is_admin: boolean };

type Props = { currentUser: CurrentUser };

export default function UserManagementPage({ currentUser }: Props) {
  if (!currentUser.is_admin) return <Navigate to="/" replace />;

  const [users, setUsers] = useState<UserInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [allowReg, setAllowReg] = useState(false);
  const [regLoading, setRegLoading] = useState(false);

  // 创建用户弹窗
  const [createOpen, setCreateOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createForm] = Form.useForm();

  // 重置密码弹窗
  const [resetTarget, setResetTarget] = useState<UserInfo | null>(null);
  const [resetLoading, setResetLoading] = useState(false);
  const [resetForm] = Form.useForm();

  async function loadUsers() {
    setLoading(true);
    try {
      const [list, setting] = await Promise.all([fetchUsers(), fetchRegistrationSetting()]);
      setUsers(list);
      setAllowReg(setting.allow_registration);
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadUsers(); }, []);

  async function handleToggleReg(checked: boolean) {
    setRegLoading(true);
    try {
      await toggleRegistration(checked);
      setAllowReg(checked);
      message.success(checked ? "已开启开放注册" : "已关闭开放注册");
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setRegLoading(false);
    }
  }

  async function handleCreate(values: { username: string; password: string; is_admin: boolean }) {
    setCreateLoading(true);
    try {
      await createUser(values);
      message.success("用户创建成功");
      setCreateOpen(false);
      createForm.resetFields();
      loadUsers();
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setCreateLoading(false);
    }
  }

  async function handleResetPassword(values: { password: string }) {
    if (!resetTarget) return;
    setResetLoading(true);
    try {
      await updateUser(resetTarget.id, { password: values.password });
      message.success(`已重置 ${resetTarget.username} 的密码`);
      setResetTarget(null);
      resetForm.resetFields();
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setResetLoading(false);
    }
  }

  async function handleToggleActive(user: UserInfo) {
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      message.success(user.is_active ? `已停用 ${user.username}` : `已启用 ${user.username}`);
      loadUsers();
    } catch (err) {
      message.error(getApiErrorMessage(err));
    }
  }

  async function handleToggleAdmin(user: UserInfo) {
    try {
      await updateUser(user.id, { is_admin: !user.is_admin });
      message.success(user.is_admin ? `已取消 ${user.username} 的管理员权限` : `已将 ${user.username} 设为管理员`);
      loadUsers();
    } catch (err) {
      message.error(getApiErrorMessage(err));
    }
  }

  const columns: ColumnsType<UserInfo> = [
    {
      title: "用户名",
      dataIndex: "username",
      key: "username",
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: "权限",
      key: "is_admin",
      render: (_: unknown, record: UserInfo) =>
        record.is_admin ? <Tag color="gold">管理员</Tag> : <Tag>普通用户</Tag>,
    },
    {
      title: "状态",
      key: "is_active",
      render: (_: unknown, record: UserInfo) =>
        record.is_active ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "注册时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (v: string) => new Date(v).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: UserInfo) => {
        const isSelf = record.id === currentUser.id;
        return (
          <Space size="small" wrap>
            <Button size="small" onClick={() => { setResetTarget(record); resetForm.resetFields(); }}>
              重置密码
            </Button>
            {!isSelf && (
              <Popconfirm
                title={record.is_active ? `确认停用 ${record.username}？` : `确认启用 ${record.username}？`}
                onConfirm={() => handleToggleActive(record)}
                okText="确认"
                cancelText="取消"
              >
                <Button size="small" danger={record.is_active}>
                  {record.is_active ? "停用" : "启用"}
                </Button>
              </Popconfirm>
            )}
            {!isSelf && (
              <Popconfirm
                title={record.is_admin
                  ? `确认取消 ${record.username} 的管理员权限？`
                  : `确认将 ${record.username} 设为管理员？`}
                onConfirm={() => handleToggleAdmin(record)}
                okText="确认"
                cancelText="取消"
              >
                <Button size="small">
                  {record.is_admin ? "取消管理员" : "设为管理员"}
                </Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>用户管理</Title>
        <Space>
          <Text type="secondary" style={{ fontSize: 13 }}>开放注册</Text>
          <Switch
            checked={allowReg}
            loading={regLoading}
            onChange={handleToggleReg}
            checkedChildren="开"
            unCheckedChildren="关"
          />
          <Button type="primary" onClick={() => { setCreateOpen(true); createForm.resetFields(); }}>
            创建用户
          </Button>
        </Space>
      </div>

      <Table
        rowKey="id"
        dataSource={users}
        columns={columns}
        loading={loading}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        size="middle"
      />

      {/* 创建用户 Modal */}
      <Modal
        title="创建用户"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        okText="创建"
        cancelText="取消"
        confirmLoading={createLoading}
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate} style={{ marginTop: 16 }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input placeholder="用户名" autoComplete="off" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password placeholder="密码" />
          </Form.Item>
          <Form.Item name="is_admin" valuePropName="checked" initialValue={false}>
            <Checkbox>设为管理员</Checkbox>
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置密码 Modal */}
      <Modal
        title={`重置密码 — ${resetTarget?.username}`}
        open={!!resetTarget}
        onCancel={() => { setResetTarget(null); resetForm.resetFields(); }}
        onOk={() => resetForm.submit()}
        okText="确认重置"
        cancelText="取消"
        confirmLoading={resetLoading}
      >
        <Form form={resetForm} layout="vertical" onFinish={handleResetPassword} style={{ marginTop: 16 }}>
          <Form.Item name="password" label="新密码" rules={[{ required: true, message: "请输入新密码" }]}>
            <Input.Password placeholder="新密码" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
