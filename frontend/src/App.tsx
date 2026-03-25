import { Layout, Menu, theme } from "antd";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import BacktestPage from "./pages/BacktestPage";
import DataCenterPage from "./pages/DataCenterPage";
import IndicatorLibPage from "./pages/IndicatorLibPage";
import KlinePage from "./pages/KlinePage";
import SyncPage from "./pages/SyncPage";

const { Header, Content } = Layout;

export default function App() {
  const location = useLocation();
  const { token } = theme.useToken();

  const selected = location.pathname.startsWith("/sync")
    ? ["sync"]
    : location.pathname.startsWith("/data-center")
      ? ["data-center"]
    : location.pathname.startsWith("/backtest")
      ? ["backtest"]
    : location.pathname.startsWith("/indicators")
      ? ["indicators"]
      : ["kline"];

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          background: token.colorBgContainer,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <div style={{ marginRight: 24, fontWeight: 600 }}>回测网站</div>
        <Menu
          mode="horizontal"
          selectedKeys={selected}
          style={{ flex: 1, minWidth: 0, border: "none" }}
          items={[
            { key: "kline", label: <Link to="/">K 线</Link> },
            { key: "backtest", label: <Link to="/backtest">回测</Link> },
            { key: "indicators", label: <Link to="/indicators">指标库</Link> },
            { key: "sync", label: <Link to="/sync">同步任务</Link> },
            { key: "data-center", label: <Link to="/data-center">数据后台</Link> },
          ]}
        />
      </Header>
      <Content style={{ padding: 24, background: token.colorBgLayout }}>
        <Routes>
          <Route path="/" element={<KlinePage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/buy" element={<Navigate to="/backtest" replace />} />
          <Route path="/sell" element={<Navigate to="/backtest" replace />} />
          <Route path="/indicators" element={<IndicatorLibPage />} />
          <Route path="/sync" element={<SyncPage />} />
          <Route path="/data-center" element={<DataCenterPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Content>
    </Layout>
  );
}
