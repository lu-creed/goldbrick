/**
 * 全站主题颜色常量
 *
 * 为什么单独建这个文件？
 * 因为颜色在很多页面里都要用，如果每个页面都自己写一遍颜色值（比如 "#f5222d"），
 * 改一次就要改很多地方，很容易漏掉。集中在这里统一管理，改一处即可全站生效。
 *
 * ⚠️ A 股颜色惯例与国际惯例相反：
 *   - 中国 A 股：涨 = 红色，跌 = 绿色
 *   - 欧美市场：涨 = 绿色，跌 = 红色
 * 本项目严格遵循 A 股惯例。
 */

// ── 涨跌颜色 ──────────────────────────────────────────────────────────────────

/** 上涨颜色（A 股红），用于涨幅为正的数字、K 线阳线、涨停统计等 */
export const RISE_COLOR = "#f5222d";

/** 下跌颜色（A 股绿），用于涨幅为负的数字、K 线阴线、跌停统计等 */
export const FALL_COLOR = "#52c41a";

/** 平盘颜色（无变化），用于涨跌幅为 0 的情况 */
export const FLAT_COLOR = "#8c8c8c";

// ── 背景颜色 ──────────────────────────────────────────────────────────────────

/** 全站主背景色（深色），参考 Ant Design 暗色主题标准 */
export const BG_DARK = "#141414";

/** 卡片/容器背景色（比主背景略亮一点，形成层次感） */
export const BG_CARD = "#1f1f1f";

/** 边框颜色（暗色主题下的分隔线） */
export const BORDER_COLOR = "#303030";

// ── 主题色 ────────────────────────────────────────────────────────────────────

/** 主色调（蓝色），用于按钮、链接、选中状态等 */
export const PRIMARY_COLOR = "#1677ff";

// ── ECharts 公共配置 ──────────────────────────────────────────────────────────

/**
 * ECharts 图表的公共基础配置（暗色主题版本）
 *
 * 什么是 ECharts？
 * ECharts 是百度开发的图表库，用来画折线图、K 线图、柱状图等各种图表。
 * 每个图表都需要一个 "option"（配置对象）来描述图表长什么样。
 * 这里定义了所有图表共用的基础配置，避免每个图表重复写。
 */
export const ECHARTS_BASE_OPTION = {
  // 图表背景色，与全站背景保持一致
  backgroundColor: "transparent",

  // 文字样式（所有文字默认用这个颜色）
  textStyle: {
    color: "#d9d9d9", // 浅灰色，在暗色背景上清晰可读
  },

  // tooltip 是鼠标悬停时弹出的提示框
  tooltip: {
    backgroundColor: "#2a2a2a", // 提示框背景
    borderColor: "#444",         // 提示框边框
    textStyle: { color: "#e0e0e0" }, // 提示框文字颜色
  },

  // 坐标轴通用样式（折线图、柱状图等用到坐标轴的场景）
  xAxis: {
    axisLine: { lineStyle: { color: "#444" } },   // x 轴线颜色
    axisTick: { lineStyle: { color: "#444" } },   // x 轴刻度颜色
    axisLabel: { color: "#8c8c8c" },              // x 轴标签颜色
    splitLine: { lineStyle: { color: "#2a2a2a" } }, // 网格线颜色
  },
  yAxis: {
    axisLine: { lineStyle: { color: "#444" } },
    axisTick: { lineStyle: { color: "#444" } },
    axisLabel: { color: "#8c8c8c" },
    splitLine: { lineStyle: { color: "#2a2a2a" } },
  },
};

// ── K 线图专用颜色 ────────────────────────────────────────────────────────────

/**
 * K 线蜡烛图颜色配置
 * 阳线（收盘价 > 开盘价，即当天上涨）用涨色，阴线（下跌）用跌色
 */
export const KLINE_COLORS = {
  upColor: RISE_COLOR,   // 阳线颜色（涨）
  downColor: FALL_COLOR, // 阴线颜色（跌）
  upBorderColor: RISE_COLOR,
  downBorderColor: FALL_COLOR,
};

/**
 * K 线图均线（MA）颜色列表
 * 多条均线用不同颜色区分，最多支持 6 条
 */
export const MA_COLORS = [
  "#ffd666", // MA5  - 金黄
  "#69c0ff", // MA10 - 天蓝
  "#95de64", // MA20 - 草绿
  "#ff85c0", // MA30 - 粉红
  "#b37feb", // MA60 - 紫色
  "#ff9c6e", // MA120 - 橙色
];

// ── 表格行样式 ────────────────────────────────────────────────────────────────

/**
 * 给 Ant Design Table 的 rowClassName 属性用
 * 实现"斑马纹"效果：奇数行和偶数行颜色略有差异，方便眼睛跟踪每一行
 *
 * 用法示例：
 *   <Table rowClassName={zebraRowClass} ... />
 */
export function zebraRowClass(_record: unknown, index: number): string {
  // 偶数行（0, 2, 4...）返回 "row-even"，奇数行返回 ""（不加 class）
  return index % 2 === 0 ? "row-even" : "";
}
