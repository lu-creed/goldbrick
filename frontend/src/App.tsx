/**
 * 应用主框架文件
 *
 * 这个文件负责整个应用的"骨架"：
 * - 顶部导航栏（Header）：包含应用名称和菜单
 * - 内容区域（Content）：根据 URL 显示对应的页面
 * - 底部免责声明（Footer）：固定显示法律声明
 *
 * React Router 的工作原理：
 * 当用户点击菜单或浏览器地址栏变化时，<Routes> 会根据当前 URL
 * 找到匹配的 <Route>，然后渲染对应的页面组件。
 */
import { Layout, Menu, Typography, theme } from "antd";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import DataCenterPage from "./pages/DataCenterPage";
import IndicatorLibPage from "./pages/IndicatorLibPage";
import KlinePage from "./pages/KlinePage";
import PrdPlaceholderPage from "./pages/PrdPlaceholderPage";
import ReplayPage from "./pages/ReplayPage";
import ScreeningPage from "./pages/ScreeningPage";
import SentimentPage from "./pages/SentimentPage";
import StockListPage from "./pages/StockListPage";
import SyncPage from "./pages/SyncPage";
import BacktestPage from "./pages/BacktestPage";
import DavPage from "./pages/DavPage";

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

/**
 * 根据当前 URL 路径，返回应该高亮的菜单项 key
 *
 * 为什么需要这个函数？
 * Ant Design 的 Menu 组件需要知道当前哪个菜单项是"选中状态"（高亮显示），
 * 这样用户能直观看到自己在哪个页面。这个函数把 URL 路径翻译成菜单 key。
 *
 * @param loc - 当前位置对象，包含 pathname（路径）和 hash（#后面的部分）
 * @returns 应该选中的菜单项 key 数组
 */
function menuSelectedKeys(loc: { pathname: string; hash: string }): string[] {
  const { pathname, hash } = loc;
  // /sync 页面有两个子区域，通过 hash 区分
  if (pathname === "/sync") return hash === "#sync-runs" ? ["m-sync-logs"] : ["m-data-sync"];
  if (pathname === "/data-center") return ["m-data-pool"];
  if (pathname === "/stock-list") return ["m-stock-list"];
  if (pathname === "/replay") return ["m-replay"];
  if (pathname === "/sentiment") return ["m-sentiment"];
  if (pathname === "/indicators") return ["m-indicators"];
  if (pathname === "/screening") return ["m-screening"];
  if (pathname === "/dav") return ["m-dav"];
  if (pathname === "/backtest/history") return ["m-backtest-records"];
  if (pathname === "/backtest") return ["m-backtest-start"];
  if (pathname === "/") return ["m-kline"];
  return ["m-kline"];
}

/**
 * 应用根组件
 *
 * 渲染整体布局：顶部导航 + 页面内容 + 底部声明
 */
export default function App() {
  // useLocation 获取当前 URL 信息，用于确定菜单选中状态
  const location = useLocation();
  // theme.useToken 获取 Ant Design 主题颜色变量，让各部分颜色跟随主题自动变化
  const { token } = theme.useToken();
  // 计算当前应该高亮哪个菜单项
  const selected = menuSelectedKeys(location);

  return (
    // Layout 是整页布局容器，minHeight: "100vh" 确保页面至少占满整个屏幕高度
    <Layout style={{ minHeight: "100vh" }}>

      {/* ── 顶部导航栏 ──────────────────────────────────────── */}
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          // 使用主题的容器背景色（暗色主题下是深灰色）
          background: token.colorBgContainer,
          // 底部边框线，视觉上把导航栏和内容区分开
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          // 固定高度，防止因内容变化导致布局抖动
          padding: "0 24px",
          position: "sticky", // 固定在顶部，滚动时不消失
          top: 0,
          zIndex: 100,        // 确保在其他内容上方
        }}
      >
        {/* 应用名称 */}
        <div style={{ marginRight: 32, fontWeight: 700, fontSize: 16, color: token.colorPrimary }}>
          GoldBrick
        </div>

        {/*
          顶部横向菜单
          - mode="horizontal"：水平排列
          - selectedKeys：高亮当前所在页面的菜单项
          - items：菜单结构（支持分组和子菜单）
        */}
        <Menu
          mode="horizontal"
          selectedKeys={selected}
          style={{ flex: 1, minWidth: 0, border: "none", background: "transparent" }}
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
                { key: "m-replay",    label: <Link to="/replay">股票复盘</Link> },
                { key: "m-dav",       label: <Link to="/dav">大V看板</Link> },
                { key: "m-stock-list", label: <Link to="/stock-list">个股列表</Link> },
                { key: "m-kline",     label: <Link to="/">K 线</Link> },
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

      {/* ── 页面内容区域 ─────────────────────────────────────── */}
      <Content
        style={{
          padding: 24,
          background: token.colorBgLayout, // 内容区背景（比卡片背景略暗）
          minHeight: "calc(100vh - 64px - 56px)", // 减去 Header 和 Footer 高度
        }}
      >
        {/*
          page-transition class 添加路由切换淡入动画（定义在 index.css）
          key={location.pathname} 让每次路由变化时重新触发动画
        */}
        <div key={location.pathname} className="page-transition">
          {/*
            路由表：根据当前 URL 显示对应页面
            - path="/" 匹配根路径，显示 K 线页
            - path="*" 匹配所有未定义路径，重定向到首页
          */}
          <Routes>
            <Route path="/" element={<KlinePage />} />
            <Route path="/replay" element={<ReplayPage />} />
            <Route path="/dav" element={<DavPage />} />
            <Route path="/stock-list" element={<StockListPage />} />
            {/* 旧路径重定向，防止书签失效 */}
            <Route path="/buy" element={<Navigate to="/" replace />} />
            <Route path="/sell" element={<Navigate to="/" replace />} />
            <Route path="/indicators" element={<IndicatorLibPage />} />
            <Route path="/sync" element={<SyncPage />} />
            <Route path="/data-center" element={<DataCenterPage />} />
            <Route path="/screening" element={<ScreeningPage />} />
            <Route path="/sentiment" element={<SentimentPage />} />
            {/* 回测功能 */}
            <Route path="/backtest" element={<BacktestPage />} />
            <Route
              path="/backtest/history"
              element={<PrdPlaceholderPage title="回测记录" prdRef="V0.0.2 · 回测记录" />}
            />
            {/* 兜底：任何未知 URL 都跳回首页 */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </Content>

      {/* ── 底部免责声明 ──────────────────────────────────────── */}
      {/*
        根据需求文档，全站合规要求：每个页面底部必须固定显示精简免责声明。
        这里放在 Layout Footer 里，所有页面自动继承，无需每个页面单独写。
      */}
      <Footer
        style={{
          textAlign: "center",
          padding: "12px 24px",
          background: token.colorBgContainer,
          borderTop: `1px solid ${token.colorBorderSecondary}`,
        }}
      >
        <Text type="secondary" style={{ fontSize: 11 }}>
          本工具仅提供股票数据查询、指标计算及历史回测服务，所有内容均为客观数据呈现，不构成任何投资建议。
          历史数据不代表未来收益，股市有风险，请独立判断。
        </Text>
      </Footer>
    </Layout>
  );
}
