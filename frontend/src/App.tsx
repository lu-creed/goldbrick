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
import { Checkbox, Drawer, Dropdown, Layout, Menu, Modal, Typography, theme } from "antd";
import { DownOutlined, MenuOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useIsMobile } from "./hooks/useIsMobile";
import DataCenterPage from "./pages/DataCenterPage";
import IndicatorLibPage from "./pages/IndicatorLibPage";
import KlinePage from "./pages/KlinePage";
import ReplayPage from "./pages/ReplayPage";
import WatchlistPage from "./pages/WatchlistPage";
import ScreeningPage from "./pages/ScreeningPage";
import SentimentPage from "./pages/SentimentPage";
import StockListPage from "./pages/StockListPage";
import SyncPage from "./pages/SyncPage";
import SyncLogsPage from "./pages/SyncLogsPage";
import BacktestPage from "./pages/BacktestPage";
import BacktestHistoryPage from "./pages/BacktestHistoryPage";
import DavPage from "./pages/DavPage";
import LoginPage from "./pages/LoginPage";
import UserManagementPage from "./pages/UserManagementPage";
import AutoUpdatePage from "./pages/AutoUpdatePage";
import { UserInfo, fetchCurrentUser } from "./api/client";

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
function menuSelectedKeys(loc: { pathname: string }): string[] {
  const { pathname } = loc;
  // /sync 页面有两个子区域，通过 hash 区分
  if (pathname === "/sync") return ["m-data-sync"];
  if (pathname === "/sync/logs") return ["m-sync-logs"];
  if (pathname === "/data-center") return ["m-data-pool"];
  if (pathname === "/stock-list") return ["m-stock-list"];
  if (pathname === "/replay") return ["m-replay"];
  if (pathname === "/watchlist") return ["m-watchlist"];
  if (pathname === "/sentiment") return ["m-sentiment"];
  if (pathname === "/indicators") return ["m-indicators"];
  if (pathname === "/screening") return ["m-screening"];
  if (pathname === "/dav") return ["m-dav"];
  if (pathname === "/backtest/history") return ["m-backtest-records"];
  if (pathname === "/backtest") return ["m-backtest-start"];
  if (pathname === "/admin/users") return ["m-user-mgmt"];
  if (pathname === "/admin/auto-update") return ["m-auto-update"];
  if (pathname === "/") return ["m-kline"];
  return ["m-kline"];
}

/**
 * 应用根组件
 *
 * 渲染整体布局：顶部导航 + 页面内容 + 底部声明
 * 首次访问时弹出免责声明 Modal，必须勾选「已阅读」才可关闭。
 */
export default function App() {
  // ── 认证状态 ────────────────────────────────────────────────
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(() => {
    const raw = localStorage.getItem("gb_user");
    return raw ? JSON.parse(raw) : null;
  });
  const [authChecking, setAuthChecking] = useState(!currentUser);

  // 应用启动时用 /auth/me 验证 token 是否仍有效
  useEffect(() => {
    if (!localStorage.getItem("gb_token")) {
      setAuthChecking(false);
      return;
    }
    fetchCurrentUser()
      .then((user) => {
        setCurrentUser(user);
        localStorage.setItem("gb_user", JSON.stringify(user));
      })
      .catch(() => {
        localStorage.removeItem("gb_token");
        localStorage.removeItem("gb_user");
        setCurrentUser(null);
      })
      .finally(() => setAuthChecking(false));
  }, []);

  function handleLogout() {
    localStorage.removeItem("gb_token");
    localStorage.removeItem("gb_user");
    setCurrentUser(null);
  }

  // 等待 token 验证完成前显示空白（避免闪烁）
  if (authChecking) return null;

  // 未登录 → 显示登录页
  if (!currentUser) {
    return <LoginPage onLogin={(user) => setCurrentUser(user as UserInfo)} />;
  }

  return <AppShell currentUser={currentUser} onLogout={handleLogout} />;
}

function AppShell({ currentUser, onLogout }: { currentUser: UserInfo; onLogout: () => void }) {
  // useLocation 获取当前 URL 信息，用于确定菜单选中状态
  const location = useLocation();
  // theme.useToken 获取 Ant Design 主题颜色变量，让各部分颜色跟随主题自动变化
  const { token } = theme.useToken();
  // 计算当前应该高亮哪个菜单项
  const selected = menuSelectedKeys(location);
  const isMobile = useIsMobile();
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);

  // ── 首次访问免责声明 Modal ─────────────────────────────────
  // 每个用户独立记录是否已阅读（key 含用户名，避免换账号后跳过）
  const disclaimerKey = `goldbrick_disclaimer_read_${currentUser.username}`;
  const [disclaimerOpen, setDisclaimerOpen] = useState(
    () => localStorage.getItem(disclaimerKey) !== "1",
  );
  const [disclaimerChecked, setDisclaimerChecked] = useState(false);
  const [disclaimerViewOpen, setDisclaimerViewOpen] = useState(false);

  /** 用户勾选「已阅读」并点击确认后，记录标志位并关闭弹窗 */
  function handleDisclaimerOk() {
    localStorage.setItem(disclaimerKey, "1");
    setDisclaimerOpen(false);
  }

  // 构建菜单 items（桌面 Menu 和移动 Drawer 共用同一份）
  const menuItems = [
    ...(currentUser.is_admin ? [{
      key: "g-backend",
      label: "数据后台",
      children: [
        { key: "m-data-sync", label: <Link to="/sync" onClick={() => setNavDrawerOpen(false)}>数据同步</Link> },
        { key: "m-data-pool", label: <Link to="/data-center" onClick={() => setNavDrawerOpen(false)}>数据池</Link> },
        { key: "m-sync-logs", label: <Link to="/sync/logs" onClick={() => setNavDrawerOpen(false)}>同步日志</Link> },
      ],
    }] : []),
    {
      key: "g-dashboard",
      label: "数据看板",
      children: [
        { key: "m-replay",    label: <Link to="/replay" onClick={() => setNavDrawerOpen(false)}>股票复盘</Link> },
        { key: "m-watchlist", label: <Link to="/watchlist" onClick={() => setNavDrawerOpen(false)}>自选股池</Link> },
        { key: "m-dav",       label: <Link to="/dav" onClick={() => setNavDrawerOpen(false)}>大V看板</Link> },
        { key: "m-stock-list", label: <Link to="/stock-list" onClick={() => setNavDrawerOpen(false)}>个股列表</Link> },
        { key: "m-kline",     label: <Link to="/" onClick={() => setNavDrawerOpen(false)}>K 线</Link> },
      ],
    },
    {
      key: "g-screen",
      label: "条件选股",
      children: [
        { key: "m-indicators", label: <Link to="/indicators" onClick={() => setNavDrawerOpen(false)}>指标库</Link> },
        { key: "m-screening", label: <Link to="/screening" onClick={() => setNavDrawerOpen(false)}>条件选股</Link> },
      ],
    },
    {
      key: "g-backtest",
      label: "股票回测",
      children: [
        { key: "m-backtest-start", label: <Link to="/backtest" onClick={() => setNavDrawerOpen(false)}>开始回测</Link> },
        { key: "m-backtest-records", label: <Link to="/backtest/history" onClick={() => setNavDrawerOpen(false)}>回测记录</Link> },
      ],
    },
    ...(currentUser.is_admin ? [{
      key: "g-admin",
      label: "系统管理",
      children: [
        { key: "m-user-mgmt", label: <Link to="/admin/users" onClick={() => setNavDrawerOpen(false)}>用户管理</Link> },
        { key: "m-auto-update", label: <Link to="/admin/auto-update" onClick={() => setNavDrawerOpen(false)}>自动更新</Link> },
      ],
    }] : []),
  ];

  return (
    // Layout 是整页布局容器，minHeight: "100vh" 确保页面至少占满整个屏幕高度
    <Layout style={{ minHeight: "100vh" }}>

      {/* ── 顶部导航栏 ──────────────────────────────────────── */}
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          background: token.colorBgContainer,
          borderBottom: `1px solid ${token.colorBorderSecondary}`,
          padding: isMobile ? "0 12px" : "0 24px",
          position: "sticky",
          top: 0,
          zIndex: 100,
        }}
      >
        {/* 应用名称 */}
        <div style={{ marginRight: isMobile ? 8 : 32, fontWeight: 700, fontSize: 16, color: token.colorPrimary, flexShrink: 0 }}>
          GoldBrick
        </div>

        {/* 桌面端：水平菜单 */}
        {!isMobile && (
          <Menu
            mode="horizontal"
            selectedKeys={selected}
            style={{ flex: 1, minWidth: 0, border: "none", background: "transparent" }}
            items={menuItems}
          />
        )}

        {/* 移动端：占位撑开空间 */}
        {isMobile && <div style={{ flex: 1 }} />}

        {/* 当前用户下拉菜单 */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginLeft: isMobile ? 0 : 16, flexShrink: 0 }}>
          <Dropdown
            menu={{
              items: [
                {
                  key: "disclaimer",
                  label: "查看免责声明",
                  onClick: () => setDisclaimerViewOpen(true),
                },
                { type: "divider" },
                {
                  key: "logout",
                  label: "退出登录",
                  danger: true,
                  onClick: onLogout,
                },
              ],
            }}
            trigger={["click"]}
          >
            <button
              style={{
                background: "none",
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 4,
                color: token.colorTextSecondary,
                cursor: "pointer",
                fontSize: 12,
                padding: "2px 10px",
                display: "flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              {!isMobile && `${currentUser.username}${currentUser.is_admin ? "（管理员）" : ""}`}
              {isMobile && currentUser.username}
              <DownOutlined style={{ fontSize: 10 }} />
            </button>
          </Dropdown>

          {/* 移动端汉堡按钮 */}
          {isMobile && (
            <button
              onClick={() => setNavDrawerOpen(true)}
              style={{
                background: "none",
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 4,
                color: token.colorText,
                cursor: "pointer",
                fontSize: 16,
                padding: "2px 8px",
                lineHeight: 1,
                display: "flex",
                alignItems: "center",
              }}
            >
              <MenuOutlined />
            </button>
          )}
        </div>
      </Header>

      {/* 移动端导航 Drawer */}
      <Drawer
        title={
          <span style={{ fontSize: 14 }}>
            {currentUser.username}{currentUser.is_admin ? "（管理员）" : ""}
          </span>
        }
        placement="right"
        open={navDrawerOpen}
        onClose={() => setNavDrawerOpen(false)}
        width={240}
        styles={{ body: { padding: 0 } }}
      >
        <Menu
          mode="inline"
          selectedKeys={selected}
          defaultOpenKeys={["g-dashboard", "g-screen", "g-backtest", "g-backend", "g-admin"]}
          style={{ border: "none", height: "100%" }}
          items={menuItems}
        />
      </Drawer>

      {/* ── 页面内容区域 ─────────────────────────────────────── */}
      <Content
        style={{
          padding: isMobile ? 12 : 24,
          background: token.colorBgLayout,
          minHeight: "calc(100vh - 64px - 56px)",
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
            <Route path="/watchlist" element={<WatchlistPage />} />
            <Route path="/dav" element={<DavPage />} />
            <Route path="/stock-list" element={<StockListPage />} />
            {/* 旧路径重定向，防止书签失效 */}
            <Route path="/buy" element={<Navigate to="/" replace />} />
            <Route path="/sell" element={<Navigate to="/" replace />} />
            <Route path="/indicators" element={<IndicatorLibPage />} />
            <Route path="/sync" element={<SyncPage />} />
            <Route path="/sync/logs" element={<SyncLogsPage />} />
            <Route path="/data-center" element={<DataCenterPage />} />
            <Route path="/screening" element={<ScreeningPage />} />
            <Route path="/sentiment" element={<SentimentPage />} />
            {/* 回测功能 */}
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/backtest/history" element={<BacktestHistoryPage />} />
            {/* 系统管理（仅管理员可访问） */}
            <Route path="/admin/users" element={<UserManagementPage currentUser={currentUser} />} />
            <Route path="/admin/auto-update" element={<AutoUpdatePage currentUser={currentUser} />} />
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

      {/* ── 首次访问免责声明弹窗 ──────────────────────────────── */}
      {/*
        closable=false + maskClosable=false：强制用户必须勾选并点击确认才能关闭，
        不允许点击遮罩或按 ESC 绕过（合规要求）。
      */}
      <Modal
        title="使用须知 · 免责声明"
        open={disclaimerOpen}
        closable={false}
        maskClosable={false}
        keyboard={false}
        okText="确认，开始使用"
        okButtonProps={{ disabled: !disclaimerChecked }}
        cancelButtonProps={{ style: { display: "none" } }}
        onOk={handleDisclaimerOk}
        width={Math.min(540, window.innerWidth * 0.95)}
      >
        <div style={{ fontSize: 14, lineHeight: 1.8, color: "#d9d9d9" }}>
          <p style={{ marginBottom: 12 }}>
            <strong>GoldBrick</strong> 是一款个人使用的股票数据工具，提供以下服务：
          </p>
          <ul style={{ paddingLeft: 20, marginBottom: 16 }}>
            <li>行情数据查询（K 线、换手率、涨跌幅等）</li>
            <li>基于自定义指标的条件选股</li>
            <li>历史数据回测（模拟盈亏，不含实盘执行）</li>
            <li>大V看板（辅助参考，非推荐依据）</li>
          </ul>
          <div
            style={{
              background: "rgba(255, 77, 79, 0.08)",
              border: "1px solid rgba(255, 77, 79, 0.3)",
              borderRadius: 6,
              padding: "10px 14px",
              marginBottom: 16,
            }}
          >
            <Text style={{ color: "#ff7875", fontSize: 13 }}>
              ⚠ 本工具所有内容均为客观数据呈现，<strong>不构成任何投资建议</strong>。
              历史回测结果不代表未来实际收益。股市存在亏损风险，请根据自身情况独立判断，
              盈亏自负，与本工具无关。
            </Text>
          </div>
          <Checkbox
            checked={disclaimerChecked}
            onChange={(e) => setDisclaimerChecked(e.target.checked)}
          >
            我已阅读并理解以上声明，不会将本工具内容作为投资依据
          </Checkbox>
        </div>
      </Modal>

      {/* ── 用户菜单「查看免责声明」弹窗（只读，可直接关闭） ── */}
      <Modal
        title="使用须知 · 免责声明"
        open={disclaimerViewOpen}
        onCancel={() => setDisclaimerViewOpen(false)}
        onOk={() => setDisclaimerViewOpen(false)}
        okText="关闭"
        cancelButtonProps={{ style: { display: "none" } }}
        width={Math.min(540, window.innerWidth * 0.95)}
      >
        <div style={{ fontSize: 14, lineHeight: 1.8, color: "#d9d9d9" }}>
          <p style={{ marginBottom: 12 }}>
            <strong>GoldBrick</strong> 是一款个人使用的股票数据工具，提供以下服务：
          </p>
          <ul style={{ paddingLeft: 20, marginBottom: 16 }}>
            <li>行情数据查询（K 线、换手率、涨跌幅等）</li>
            <li>基于自定义指标的条件选股</li>
            <li>历史数据回测（模拟盈亏，不含实盘执行）</li>
            <li>大V看板（辅助参考，非推荐依据）</li>
          </ul>
          <div
            style={{
              background: "rgba(255, 77, 79, 0.08)",
              border: "1px solid rgba(255, 77, 79, 0.3)",
              borderRadius: 6,
              padding: "10px 14px",
            }}
          >
            <Text style={{ color: "#ff7875", fontSize: 13 }}>
              ⚠ 本工具所有内容均为客观数据呈现，<strong>不构成任何投资建议</strong>。
              历史回测结果不代表未来实际收益。股市存在亏损风险，请根据自身情况独立判断，
              盈亏自负，与本工具无关。
            </Text>
          </div>
        </div>
      </Modal>
    </Layout>
  );
}
