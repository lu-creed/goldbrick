/**
 * 应用主框架文件
 *
 * 重构（2026-05-04，访客/登录分层 PR 2）：
 * - 未登录时不再跳 LoginPage，直接渲染 AppShell 进入"访客态"
 * - 全局 <LoginGateModal> 替代硬跳 /login；由 useAuth Context 驱动
 * - 部分路由用 <ProtectedRoute> 包（DAV/Watchlist/回测历史/所有 admin 页）
 * - 顶栏右上角访客显示「登录」按钮，登录后显示用户下拉
 * - 菜单不再按 is_admin 过滤，始终全展示；访客 / 非管理员点到时由 ProtectedRoute 拦截
 * - 免责声明 key 按登录态分叉：访客 = anonymous，登录 = username
 */
import { Button, Checkbox, Drawer, Dropdown, Layout, Menu, Modal, Typography, theme } from "antd";
import { DownOutlined, LoginOutlined, MenuOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useIsMobile } from "./hooks/useIsMobile";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import LoginGateModal from "./components/LoginGateModal";
import ProtectedRoute from "./components/ProtectedRoute";
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
import StrategyGalleryPage from "./pages/StrategyGalleryPage";
import DavPage from "./pages/DavPage";
import UserManagementPage from "./pages/UserManagementPage";
import AutoUpdatePage from "./pages/AutoUpdatePage";
import { UserInfo, fetchCurrentUser } from "./api/client";

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

/** 根据当前 URL 路径返回应该高亮的菜单项 key */
function menuSelectedKeys(loc: { pathname: string }): string[] {
  const { pathname } = loc;
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
  if (pathname === "/gallery") return ["m-strategy-gallery"];
  if (pathname === "/admin/users") return ["m-user-mgmt"];
  if (pathname === "/admin/auto-update") return ["m-auto-update"];
  if (pathname === "/") return ["m-kline"];
  return ["m-kline"];
}

/**
 * 应用根组件：最外层包 AuthProvider，下层所有组件可通过 useAuth() 拿到鉴权状态。
 */
export default function App() {
  return (
    <AuthProvider>
      <AppRoot />
    </AuthProvider>
  );
}

/**
 * 首屏鉴权校验 + 挂载全局 LoginGateModal
 */
function AppRoot() {
  const { setCurrentUser } = useAuth();
  const [authChecking, setAuthChecking] = useState(
    () => localStorage.getItem("gb_token") !== null,
  );

  // 启动时若本地有 token，用 /auth/me 校验；失败则静默清凭据进入访客态，
  // 不再硬跳登录页（fetchCurrentUser 已带 _skipLoginGate，不会反弹 Modal）
  useEffect(() => {
    if (!localStorage.getItem("gb_token")) {
      setAuthChecking(false);
      return;
    }
    fetchCurrentUser()
      .then((user) => setCurrentUser(user))
      .catch(() => {
        localStorage.removeItem("gb_token");
        localStorage.removeItem("gb_user");
        setCurrentUser(null);
      })
      .finally(() => setAuthChecking(false));
  }, [setCurrentUser]);

  // 有 token 时等 /auth/me 校验完再渲染（避免登录态从"像已登录"闪到"访客态"）
  if (authChecking) return null;

  return (
    <>
      <AppShell />
      <LoginGateModal />
    </>
  );
}

/**
 * 主应用外壳：顶栏 / 菜单 / 路由 / 页脚 / 免责声明。
 * 鉴权状态来自 useAuth()，currentUser 可能为 null（访客态）。
 */
function AppShell() {
  const location = useLocation();
  const { token } = theme.useToken();
  const selected = menuSelectedKeys(location);
  const isMobile = useIsMobile();
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const { currentUser, isGuest, openLoginGate, logout } = useAuth();

  // ── 免责声明 Modal：访客 / 登录后各一套独立 key ────────────
  const usernameKey = currentUser?.username ?? "anonymous";
  const disclaimerKey = `goldbrick_disclaimer_read_${usernameKey}`;
  const [disclaimerOpen, setDisclaimerOpen] = useState(
    () => localStorage.getItem(disclaimerKey) !== "1",
  );
  const [disclaimerChecked, setDisclaimerChecked] = useState(false);
  const [disclaimerViewOpen, setDisclaimerViewOpen] = useState(false);

  // 登录态切换（访客 → 登录 / 换账号）时重新评估 disclaimer
  // 访客读过一次 + 登录后用户名变化 → 要求用户再确认一次（新的 key）
  useEffect(() => {
    const alreadyRead = localStorage.getItem(disclaimerKey) === "1";
    setDisclaimerOpen(!alreadyRead);
    setDisclaimerChecked(false);
  }, [disclaimerKey]);

  function handleDisclaimerOk() {
    localStorage.setItem(disclaimerKey, "1");
    setDisclaimerOpen(false);
  }

  // ── 菜单：始终全展示（含系统管理组），访客 / 非管理员点击时由 ProtectedRoute 弹 Modal ─
  const menuItems = [
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
        { key: "m-strategy-gallery", label: <Link to="/gallery" onClick={() => setNavDrawerOpen(false)}>策略广场 🆕</Link> },
        { key: "m-backtest-start", label: <Link to="/backtest" onClick={() => setNavDrawerOpen(false)}>开始回测</Link> },
        { key: "m-backtest-records", label: <Link to="/backtest/history" onClick={() => setNavDrawerOpen(false)}>回测记录</Link> },
      ],
    },
    {
      key: "g-admin",
      label: "系统管理",
      children: [
        { key: "m-data-sync", label: <Link to="/sync" onClick={() => setNavDrawerOpen(false)}>数据同步</Link> },
        { key: "m-data-pool", label: <Link to="/data-center" onClick={() => setNavDrawerOpen(false)}>数据池</Link> },
        { key: "m-sync-logs", label: <Link to="/sync/logs" onClick={() => setNavDrawerOpen(false)}>同步日志</Link> },
        { key: "m-user-mgmt", label: <Link to="/admin/users" onClick={() => setNavDrawerOpen(false)}>用户管理</Link> },
        { key: "m-auto-update", label: <Link to="/admin/auto-update" onClick={() => setNavDrawerOpen(false)}>自动更新</Link> },
      ],
    },
  ];

  return (
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
        <div style={{ marginRight: isMobile ? 8 : 32, fontWeight: 700, fontSize: 16, color: token.colorPrimary, flexShrink: 0 }}>
          GoldBrick
        </div>

        {!isMobile && (
          <Menu
            mode="horizontal"
            selectedKeys={selected}
            style={{ flex: 1, minWidth: 0, border: "none", background: "transparent" }}
            items={menuItems}
          />
        )}

        {isMobile && <div style={{ flex: 1 }} />}

        {/* 右上角：访客态显示「登录」按钮；已登录显示用户下拉 */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginLeft: isMobile ? 0 : 16, flexShrink: 0 }}>
          {isGuest ? (
            <Button
              type="primary"
              size="small"
              icon={<LoginOutlined />}
              onClick={() => openLoginGate()}
            >
              登录
            </Button>
          ) : (
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
                    onClick: logout,
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
                {!isMobile && `${currentUser!.username}${currentUser!.is_admin ? "（管理员）" : ""}`}
                {isMobile && currentUser!.username}
                <DownOutlined style={{ fontSize: 10 }} />
              </button>
            </Dropdown>
          )}

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
            {isGuest
              ? "游客"
              : `${currentUser!.username}${currentUser!.is_admin ? "（管理员）" : ""}`}
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
          defaultOpenKeys={["g-dashboard", "g-screen", "g-backtest", "g-admin"]}
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
        <div key={location.pathname} className="page-transition">
          <Routes>
            {/* ── 公开路由：访客直接可见 ────────────── */}
            <Route path="/" element={<KlinePage />} />
            <Route path="/replay" element={<ReplayPage />} />
            <Route path="/sentiment" element={<SentimentPage />} />
            <Route path="/stock-list" element={<StockListPage />} />
            <Route path="/indicators" element={<IndicatorLibPage />} />
            <Route path="/gallery" element={<StrategyGalleryPage />} />
            {/* ── 软挡路由：访客能看到表单 UI，关键按钮在 PR 3 拦截 ──── */}
            <Route path="/screening" element={<ScreeningPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            {/* ── 硬挡路由：纯数据列表，访客看空白无意义 ────────── */}
            <Route path="/dav" element={
              <ProtectedRoute gateMessage="登录后管理你的大V看板（ABCD 分类、派息率跟踪）">
                <DavPage />
              </ProtectedRoute>
            } />
            <Route path="/watchlist" element={
              <ProtectedRoute gateMessage="登录后查看和编辑自选股池">
                <WatchlistPage />
              </ProtectedRoute>
            } />
            <Route path="/backtest/history" element={
              <ProtectedRoute gateMessage="登录后查看你的历史回测记录">
                <BacktestHistoryPage />
              </ProtectedRoute>
            } />
            {/* ── 管理员路由：需 is_admin 才能访问 ──────────────── */}
            <Route path="/sync" element={
              <ProtectedRoute adminOnly gateMessage="数据同步仅管理员可用">
                <SyncPage />
              </ProtectedRoute>
            } />
            <Route path="/sync/logs" element={
              <ProtectedRoute adminOnly gateMessage="同步日志仅管理员可用">
                <SyncLogsPage />
              </ProtectedRoute>
            } />
            <Route path="/data-center" element={
              <ProtectedRoute adminOnly gateMessage="数据池（运维）仅管理员可用">
                <DataCenterPage />
              </ProtectedRoute>
            } />
            <Route path="/admin/users" element={
              <ProtectedRoute adminOnly gateMessage="用户管理仅管理员可用">
                <UserManagementPage currentUser={currentUser as UserInfo} />
              </ProtectedRoute>
            } />
            <Route path="/admin/auto-update" element={
              <ProtectedRoute adminOnly gateMessage="自动更新配置仅管理员可用">
                <AutoUpdatePage currentUser={currentUser as UserInfo} />
              </ProtectedRoute>
            } />
            {/* ── 兼容旧书签 + 兜底 ────────────────────────────── */}
            <Route path="/buy" element={<Navigate to="/" replace />} />
            <Route path="/sell" element={<Navigate to="/" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </Content>

      {/* ── 底部免责声明 ──────────────────────────────────────── */}
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

      {/* ── 首次访问免责声明弹窗（强制阅读） ────────────────── */}
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
