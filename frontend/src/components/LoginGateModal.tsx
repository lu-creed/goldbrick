/**
 * 全局登录墙 Modal：
 *   - 受 useAuth().loginGate 驱动；有值即弹，onCancel 关闭
 *   - 支持上下文感知文案（title/message 随入口不同）
 *   - 内嵌登录表单（用户名 + 密码），复用 api/client.ts 的 login + fetchCurrentUser
 *   - 登录成功：写 token、更新 currentUser、执行 loginGate.onSuccess 回调、关闭 Modal
 *   - 取消/关闭：仅 closeLoginGate，不清 token（与"会话过期"场景共用）
 *   - 预留「注册」按钮：暂 tooltip 占位，公测前置灰不可点
 */
import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Form, Input, Modal, Tooltip, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { fetchCurrentUser, getApiErrorMessage, login } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const { Text } = Typography;

export default function LoginGateModal() {
  const { loginGate, closeLoginGate, setCurrentUser } = useAuth();
  const [form] = Form.useForm<{ username: string; password: string }>();
  const [submitting, setSubmitting] = useState(false);

  // Modal 每次打开重置表单（避免上次的密码残留）
  useEffect(() => {
    if (loginGate) form.resetFields();
  }, [loginGate, form]);

  if (!loginGate) return null;

  async function handleSubmit(values: { username: string; password: string }) {
    setSubmitting(true);
    try {
      const { access_token } = await login(values.username, values.password);
      localStorage.setItem("gb_token", access_token);
      const user = await fetchCurrentUser();
      setCurrentUser(user);
      message.success(`欢迎回来，${user.username}`);
      // 登录成功后先关闭 Modal，再执行 onSuccess（回调可能会做 navigate）
      const cb = loginGate?.onSuccess;
      closeLoginGate();
      if (cb) cb(user);
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title={loginGate.title || "登录 GoldBrick"}
      open
      onCancel={closeLoginGate}
      footer={null}
      width={380}
      destroyOnClose
    >
      {loginGate.message && (
        <div style={{ marginBottom: 16 }}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            {loginGate.message}
          </Text>
        </div>
      )}
      <Form form={form} layout="vertical" onFinish={handleSubmit} size="large">
        <Form.Item name="username" rules={[{ required: true, message: "请输入用户名" }]}>
          <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
        </Form.Item>
        <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
          <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
        </Form.Item>
        <Form.Item style={{ marginBottom: 8 }}>
          <Button type="primary" htmlType="submit" loading={submitting} block>
            登录
          </Button>
        </Form.Item>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Text type="secondary" style={{ fontSize: 12 }}>还没账号？</Text>
          <Tooltip title="公测后开放注册，敬请期待">
            <Button type="link" disabled style={{ padding: 0, fontSize: 12 }}>
              注册账号
            </Button>
          </Tooltip>
        </div>
      </Form>
    </Modal>
  );
}
