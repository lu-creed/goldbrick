/**
 * 全局鉴权状态 Context：
 *   - currentUser: 已登录用户对象；访客时为 null
 *   - isGuest: currentUser === null 的便捷别名
 *   - loginGate: 当前待弹出的登录 Modal 上下文（title/message/onSuccess）；null 表示不弹
 *   - openLoginGate(ctx) / closeLoginGate(): 触发 / 关闭登录 Modal
 *   - setCurrentUser(u): 登录成功后调用；同步写入 localStorage
 *   - logout(): 清 token/user + 设 currentUser=null
 *
 * 这个 Context 在 App 根节点用 <AuthProvider> 包一次即可，下层组件用 useAuth() 消费。
 * axios 响应拦截器（非 React 环境）通过 api/loginGateBus 桥接调用 openLoginGate。
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import type { UserInfo } from "../api/client";
import { LoginGateContext as GateCtx, setLoginGateHandler } from "../api/loginGateBus";

export type { GateCtx as LoginGateContext };

type AuthContextValue = {
  currentUser: UserInfo | null;
  isGuest: boolean;
  loginGate: GateCtx | null;
  openLoginGate: (ctx?: GateCtx) => void;
  closeLoginGate: () => void;
  setCurrentUser: (u: UserInfo | null) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // 初值取 localStorage 缓存；/auth/me 校验由 App 的 useEffect 在 mount 后异步完成
  const [currentUser, setCurrentUserState] = useState<UserInfo | null>(() => {
    const raw = localStorage.getItem("gb_user");
    try {
      return raw ? (JSON.parse(raw) as UserInfo) : null;
    } catch {
      return null;
    }
  });
  const [loginGate, setLoginGate] = useState<GateCtx | null>(null);

  const setCurrentUser = useCallback((u: UserInfo | null) => {
    setCurrentUserState(u);
    if (u) {
      localStorage.setItem("gb_user", JSON.stringify(u));
    } else {
      localStorage.removeItem("gb_user");
    }
  }, []);

  const openLoginGate = useCallback((ctx?: GateCtx) => {
    setLoginGate(ctx ?? {});
  }, []);

  const closeLoginGate = useCallback(() => {
    setLoginGate(null);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("gb_token");
    localStorage.removeItem("gb_user");
    setCurrentUserState(null);
  }, []);

  // 把 openLoginGate 暴露给 axios 401 拦截器（loginGateBus）
  useEffect(() => {
    setLoginGateHandler(openLoginGate);
    return () => setLoginGateHandler(null);
  }, [openLoginGate]);

  return (
    <AuthContext.Provider
      value={{
        currentUser,
        isGuest: currentUser === null,
        loginGate,
        openLoginGate,
        closeLoginGate,
        setCurrentUser,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  return ctx;
}
