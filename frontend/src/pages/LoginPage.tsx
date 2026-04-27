import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { useState } from "react";
import { fetchCurrentUser, getApiErrorMessage, login } from "../api/client";

const { Title, Text } = Typography;

type Props = {
  onLogin: (user: { id: number; username: string; is_admin: boolean }) => void;
};

export default function LoginPage({ onLogin }: Props) {
  const [loading, setLoading] = useState(false);

  async function handleSubmit(values: { username: string; password: string }) {
    setLoading(true);
    try {
      const { access_token } = await login(values.username, values.password);
      localStorage.setItem("gb_token", access_token);
      const user = await fetchCurrentUser();
      localStorage.setItem("gb_user", JSON.stringify(user));
      onLogin(user);
    } catch (err) {
      message.error(getApiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0d0d0d",
      }}
    >
      <Card style={{ width: 360 }}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <Title level={3} style={{ marginBottom: 4 }}>GoldBrick</Title>
          <Text type="secondary">登录账号以继续</Text>
        </div>
        <Form layout="vertical" onFinish={handleSubmit} size="large">
          <Form.Item name="username" rules={[{ required: true, message: "请输入用户名" }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
