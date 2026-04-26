/**
 * 应用入口文件
 *
 * 这个文件是整个前端应用启动的起点，做三件事：
 * 1. 找到 HTML 页面中 id="root" 的 div 元素，把 React 应用渲染进去
 * 2. 配置 Ant Design 组件库的全局主题（暗色主题）和语言（中文）
 * 3. 启用路由功能（BrowserRouter）让不同 URL 显示不同页面
 */
import React from "react";
import ReactDOM from "react-dom/client";
// ConfigProvider 是 Ant Design 的全局配置组件
// 套在最外层，里面所有的 Ant Design 组件都会继承这里的配置
import { ConfigProvider, theme } from "antd";
import "./index.css";
// 中文语言包：让 Ant Design 组件显示中文（如日历的"确定"/"取消"按钮等）
import zhCN from "antd/locale/zh_CN";
// BrowserRouter 提供路由能力，让 URL 变化时显示对应的页面
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { PRIMARY_COLOR } from "./constants/theme";

ReactDOM.createRoot(document.getElementById("root")!).render(
  // React.StrictMode 是开发时的检查工具，帮助发现潜在问题，不影响生产环境
  <React.StrictMode>
    {/*
      ConfigProvider：全局主题配置
      - locale={zhCN}：所有 Ant Design 组件显示中文
      - theme.darkAlgorithm：启用暗色主题算法，让所有组件自动变成深色风格
      - token：覆盖具体的颜色值，让主题符合产品设计规范
    */}
    <ConfigProvider
      locale={zhCN}
      theme={{
        // 使用暗色主题算法（自动把所有组件颜色调整为深色风格）
        algorithm: theme.darkAlgorithm,
        // token 是 Ant Design 的设计令牌，可以精细覆盖具体颜色
        token: {
          colorPrimary: PRIMARY_COLOR, // 主色调（按钮、链接等的蓝色）
          colorBgBase: "#141414",      // 基础背景色
          borderRadius: 6,             // 全局圆角大小（单位：像素）
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        },
        // components 可以单独覆盖某个组件的样式
        components: {
          // 菜单组件的暗色样式
          Menu: {
            darkItemBg: "#141414",
            darkSubMenuItemBg: "#1a1a1a",
          },
          // 表格组件：斑马纹的"偶数行"颜色略微亮一点
          Table: {
            rowHoverBg: "#2a2a3a", // 鼠标悬停行的背景色
          },
        },
      }}
    >
      {/* BrowserRouter 包裹整个应用，使 URL 路由功能生效 */}
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
