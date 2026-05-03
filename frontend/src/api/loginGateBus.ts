/**
 * 登录墙 Modal 的全局触发桥：
 * axios 响应拦截器（非 React 环境）要能在 401 时弹出登录 Modal，
 * 但 Modal 由 React Context 管理——通过这个 module-level bus 解耦：
 *   - AuthProvider 挂载时调用 setLoginGateHandler(openLoginGate) 注册回调
 *   - client.ts 的 401 拦截器调用 triggerLoginGate(ctx) 触发
 *
 * 也被 useAuth 复用作为 LoginGateContext 类型定义源（避免循环依赖）。
 */
import type { UserInfo } from "./client";

export type LoginGateContext = {
  /** Modal 标题；缺省为「登录 GoldBrick」 */
  title?: string;
  /** Modal 里的提示文案（上下文感知，如「登录后回测这个策略」） */
  message?: string;
  /** 登录成功后的回调（用于跳转、触发原来的业务动作等） */
  onSuccess?: (user: UserInfo) => void;
};

type Handler = (ctx?: LoginGateContext) => void;
let handler: Handler | null = null;

export function setLoginGateHandler(h: Handler | null) {
  handler = h;
}

export function triggerLoginGate(ctx?: LoginGateContext) {
  if (handler) handler(ctx);
}
