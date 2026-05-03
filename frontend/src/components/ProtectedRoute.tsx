/**
 * 路由级权限守卫：
 *   - 访客：触发 openLoginGate + 渲染「登录后查看」占位
 *   - 登录但 adminOnly && !is_admin：渲染「仅管理员可用」占位
 *   - 满足权限：正常渲染 children
 *
 * 使用场景：
 *   - DAV、自选股池、回测历史等纯数据列表页（访客看空白无意义）
 *   - 所有 /admin/* 路由（加 adminOnly）
 *
 * 有参数表单的页面（条件选股、回测页）不用 ProtectedRoute 包——访客能看 UI，
 * 点「开始执行」按钮时再由 PR 3 做入口级软挡。
 */
import { Button, Result } from "antd";
import { ReactNode, useEffect } from "react";
import { useAuth } from "../hooks/useAuth";

type Props = {
  children: ReactNode;
  /** 仅管理员可访问；默认 false */
  adminOnly?: boolean;
  /** 弹 Modal 时的上下文文案，例如「登录后查看自选股」 */
  gateMessage?: string;
  /** 占位页显示的标题；缺省为「此页面需要登录」 */
  placeholderTitle?: string;
};

export default function ProtectedRoute({
  children,
  adminOnly = false,
  gateMessage,
  placeholderTitle,
}: Props) {
  const { currentUser, isGuest, openLoginGate } = useAuth();

  // 访客进入受保护路由时，自动弹出登录 Modal 一次（后续用户可关闭继续浏览占位）
  useEffect(() => {
    if (isGuest) {
      openLoginGate({ message: gateMessage });
    }
    // 依赖只含 isGuest——访客状态变化（登录/登出）才重新触发
  }, [isGuest]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (isGuest) {
    return (
      <Result
        status="info"
        title={placeholderTitle || "此页面需要登录"}
        subTitle={gateMessage || "登录后即可使用此功能"}
        extra={
          <Button type="primary" onClick={() => openLoginGate({ message: gateMessage })}>
            立即登录
          </Button>
        }
      />
    );
  }

  if (adminOnly && !currentUser?.is_admin) {
    return (
      <Result
        status="403"
        title="仅管理员可用"
        subTitle="你的账号没有访问此页面的权限"
      />
    );
  }

  return <>{children}</>;
}
