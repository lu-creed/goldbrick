/** @type {import('tailwindcss').Config} */
export default {
  // 与 Ant Design 并存：关闭 Preflight，避免重置组件样式
  corePlugins: { preflight: false },
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
