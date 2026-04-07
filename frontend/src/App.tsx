/**
 * 顶栏按产品功能模块分组（与飞书 PRD 一级模块一致），不按版本号分包。
 * 同步日志 → /sync#sync-runs；个股列表 → /stock-list。
 */
import { Layout, Menu, theme } from "antd";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import DataCenterPage from "./pages/DataCenterPage";
import IndicatorLibPage from "./pages/IndicatorLibPage";
import KlinePage from "./pages/KlinePage";
import PrdPlaceholderPage from "./pages/PrdPlaceholderPage";
import ReplayPage from "./pages/ReplayPage";
import ScreeningPage from "./pages/ScreeningPage";
import StockListPage from "./pages/StockListPage";
import SyncPage from "./pages/SyncPage";

const { Header, Content } = Layout;

/** 根据路径与查询串选中叶子菜单项 */
function menuSelectedKeys(loc: { pathname: string; hash: string }): string[] {
  const { pathname, hash } = loc;
  if (pathname === "/sync") return hash === "#sync-runs" ? ["m-sync-logs"] : ["m-data-sync"];
  if (pathname === "/data-center") return ["m-data-pool"];
  if (pathname === "/stock-list") return ["m-stock-list"];
  if (pathname === "/replay") return ["m-replay"];
  if (pathname === "/indicators") return ["m-indicators"];
  if (pathname === "/screening") return ["m-screening"];
  if (pathname === "/backtest/history") return ["m-backtest-records"];
  if (pathname === "/backtest") return ["m-backtest-start"];
  if (pathname === "/") return ["m-kline"];
  return ["m-kline"];
}

export default function App() {
  const location = useLocation();
  const { token } = theme.useToken();
  const selected = menuSelectedKeys(location);

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
        <div style={{ marginRight: 24, fontWeight: 600 }}>GoldBrick</div>
        <Menu
          mode="horizontal"
          selectedKeys={selected}
          style={{ flex: 1, minWidth: 0, border: "none" }}
          items={[
            {
              key: "g-backend",
              label: "数据后台",
              children: [
                { key: "m-data-sync", label: <Link to="/sync">数据同步</Link> },
                { key: "m-data-pool", label: <Link to="/data-center">数据池</Link> },
                { key: "m-sync-logs", label: <Link to="/sync#sync-runs">同步日志</Link> },
              ],
            },
            {
              key: "g-dashboard",
              label: "数据看板",
              children: [
                { key: "m-replay", label: <Link to="/replay">股票复盘</Link> },
                { key: "m-stock-list", label: <Link to="/stock-list">个股列表</Link> },
                { key: "m-kline", label: <Link to="/">K 线</Link> },
              ],
            },
            {
              key: "g-screen",
              label: "条件选股",
              children: [
                { key: "m-indicators", label: <Link to="/indicators">指标库</Link> },
                { key: "m-screening", label: <Link to="/screening">条件选股</Link> },
              ],
            },
            {
              key: "g-backtest",
              label: "股票回测",
              children: [
                { key: "m-backtest-start", label: <Link to="/backtest">开始回测</Link> },
                { key: "m-backtest-records", label: <Link to="/backtest/history">回测记录</Link> },
              ],
            },
          ]}
        />
      </Header>
      <Content style={{ padding: 24, background: token.colorBgLayout }}>
        <Routes>
          <Route path="/" element={<KlinePage />} />
          <Route path="/replay" element={<ReplayPage />} />
          <Route path="/stock-list" element={<StockListPage />} />
          <Route path="/buy" element={<Navigate to="/" replace />} />
          <Route path="/sell" element={<Navigate to="/" replace />} />
          <Route path="/indicators" element={<IndicatorLibPage />} />
          <Route path="/sync" element={<SyncPage />} />
          <Route path="/data-center" element={<DataCenterPage />} />
          <Route path="/screening" element={<ScreeningPage />} />
          <Route
            path="/backtest"
            element={<PrdPlaceholderPage title="开始回测" prdRef="V0.0.2 · 开始回测" />}
          />
          <Route
            path="/backtest/history"
            element={<PrdPlaceholderPage title="回测记录" prdRef="V0.0.2 · 回测记录" />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Content>
    </Layout>
  );
}
