/**
 * K 线页面
 *
 * 这是整个应用的核心查看页面，功能包括：
 * - 选择股票标的、K 线周期（日/周/月/季/年）、复权方式（不复权/前复权/后复权）
 * - 主图叠加均线指标：MA、EXPMA、BOLL
 * - 副图切换：成交量、MACD、KDJ、自定义 DSL 指标
 * - 图表缩放/平移（鼠标拖拽 + 底部滑块 + 按钮）
 * - 鼠标悬停时弹出 tooltip 显示 OHLC 及换手率等信息
 *
 * 技术实现：ECharts 蜡烛图（candlestick） + 所有指标均前端计算
 */
import { Alert, Button, Card, InputNumber, Radio, Select, Space, Spin, Typography, message } from "antd";
import * as echarts from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type AdjType,
  type BarPoint,
  type Interval,
  type UserIndicatorOut,
  fetchBars,
  fetchCustomIndicatorSeries,
  fetchCustomIndicators,
  fetchSymbols,
  getApiErrorMessage,
} from "../api/client";
import { ECHARTS_BASE_OPTION, FALL_COLOR, MA_COLORS, RISE_COLOR } from "../constants/theme";
import { useIsMobile } from "../hooks/useIsMobile";

/** K 线周期选项列表（周期值与后端接口对应） */
const intervals: { label: string; value: Interval }[] = [
  { label: "日 K", value: "1d" },
  { label: "周 K", value: "1w" },
  { label: "月 K", value: "1M" },
  { label: "季 K", value: "1Q" },
  { label: "年 K", value: "1y" },
];

/** 主图指标类型（叠加在蜡烛图上方） */
type MainIndicator = "none" | "MA" | "EXPMA" | "BOLL";
/** 副图指标类型（显示在蜡烛图下方的独立区域） */
type SubIndicator = "VOL" | "MACD" | "KDJ" | "CUSTOM";

/**
 * 从自定义指标定义中提取「可在 K 线副图绘制」的子线选项
 * 过滤掉 use_in_chart=false 和 auxiliary_only=true 的子线
 *
 * @param def - 指标定义对象（来自后端的 JSON 配置）
 * @returns 子线下拉选项列表 { value: key, label: name }
 */
function chartSubKeysForKline(def: Record<string, unknown> | null | undefined): { value: string; label: string }[] {
  const subs = def?.sub_indicators as
    | { key?: string; name?: string; use_in_chart?: boolean; auxiliary_only?: boolean }[]
    | undefined;
  if (!Array.isArray(subs)) return [];
  return subs
    .filter((s) => s.key && s.use_in_chart !== false && !s.auxiliary_only)
    .map((s) => ({ value: s.key!, label: `${s.name || s.key}` }));
}

export default function KlinePage() {
  // searchParams：从 URL 读取 ?ts_code=xxx 参数（其他页面跳转过来时会带上）
  const [searchParams] = useSearchParams();
  const tsFromUrl = searchParams.get("ts_code");

  // chartRef：指向 ECharts 图表容器 DOM 元素
  const chartRef = useRef<HTMLDivElement>(null);
  // chartInstance：保存 ECharts 实例，避免每次更新 option 都重新创建
  const chartInstance = useRef<echarts.ECharts | null>(null);
  // dataZoomPreserveRef：记录用户当前的可视区间（拖拽后保留），
  // 切换标的或周期时清空（重置到默认显示最近 30%），切换复权时保留
  const dataZoomPreserveRef = useRef<{ start: number; end: number } | null>(null);

  // symbols：股票列表（下拉选择框的选项）
  const [symbols, setSymbols] = useState<{ label: string; value: string }[]>([]);
  // tsCode：当前选中的股票代码
  const [tsCode, setTsCode] = useState<string | undefined>();
  // interval：当前 K 线周期
  const [interval, setInterval] = useState<Interval>("1d");
  // bars：当前标的的 K 线数据（每个元素是一根蜡烛）
  const [bars, setBars] = useState<BarPoint[]>([]);
  // loading：是否正在加载 K 线数据
  const [loading, setLoading] = useState(false);
  // error：错误信息（非 null 时显示错误提示条）
  const [error, setError] = useState<string | null>(null);
  // chartWidth：图表当前宽度（单位像素），用于自适应坐标轴左边距
  const [chartWidth, setChartWidth] = useState(980);
  const isMobile = useIsMobile();
  const chartHeight = isMobile ? 320 : 520;

  // 主图指标参数
  const [mainIndicator, setMainIndicator] = useState<MainIndicator>("none");
  const [maPeriods, setMaPeriods] = useState<number[]>([5, 10, 20]); // MA 均线周期列表
  const [expmaPeriod, setExpmaPeriod] = useState<number>(12);         // EXPMA 周期
  const [bollN, setBollN] = useState<number>(20);                     // BOLL 计算周期 N
  const [bollK, setBollK] = useState<number>(2);                      // BOLL 标准差倍数 K

  // 副图指标参数
  const [subIndicator, setSubIndicator] = useState<SubIndicator>("VOL");
  const [macdFast, setMacdFast] = useState<number>(12);   // MACD 快线周期
  const [macdSlow, setMacdSlow] = useState<number>(26);   // MACD 慢线周期
  const [macdSignal, setMacdSignal] = useState<number>(9); // MACD 信号线周期
  const [kdjN, setKdjN] = useState<number>(9);             // KDJ 计算周期 N

  // 复权方式
  const [adjType, setAdjType] = useState<AdjType>("none");

  // 自定义指标相关状态
  const [customIndicators, setCustomIndicators] = useState<UserIndicatorOut[]>([]); // 已保存的自定义指标列表
  const [customUserIndId, setCustomUserIndId] = useState<number | undefined>();      // 当前选中的指标 ID
  const [customSubKey, setCustomSubKey] = useState<string | undefined>();            // 当前选中的子线 key
  const [customSeriesTitle, setCustomSeriesTitle] = useState<string>("自定义指标"); // 副图图例标题
  const [customPoints, setCustomPoints] = useState<{ time: string; value: number | null }[]>([]); // 原始数据点

  /**
   * 将自定义指标的数据点与 bars 时间轴对齐
   * ECharts 要求数据数组长度与 category 数组完全一致，没有对应时间点时填 null
   */
  const customAligned = useMemo(() => {
    if (!customPoints.length) return [] as (number | null)[];
    const m = new Map(customPoints.map((p) => [p.time, p.value]));
    return bars.map((b) => (m.has(b.time) ? (m.get(b.time) ?? null) : null));
  }, [bars, customPoints]);

  /**
   * 把成交量数字格式化为"X 亿"或"X 万"（用于副图 Y 轴标签）
   */
  const formatVolume = (v: number): string => {
    const n = Number(v) || 0;
    const abs = Math.abs(n);
    if (abs >= 100000000) return `${(n / 100000000).toFixed(2)}亿`;
    if (abs >= 10000) return `${(n / 10000).toFixed(2)}万`;
    return `${Math.round(n)}`;
  };

  /**
   * 前端计算所有技术指标数据
   * 每当 bars 或各指标参数变化时重新计算
   *
   * 包含：MA（简单移动平均）、EXPMA（指数移动平均）、BOLL 布林带、
   * MACD、KDJ
   */
  const indicatorData = useMemo(() => {
    const closes = bars.map((b) => b.close);
    const highs = bars.map((b) => b.high);
    const lows = bars.map((b) => b.low);

    // MA：滑动窗口求均值，窗口期前的位置返回 null（无法计算）
    const calcMA = (period: number): Array<number | null> =>
      closes.map((_, i) => {
        if (i < period - 1) return null;
        const s = closes.slice(i - period + 1, i + 1).reduce((a, c) => a + c, 0);
        return s / period;
      });
    const maMap: Record<string, Array<number | null>> = {};
    maPeriods.forEach((p) => {
      maMap[String(p)] = calcMA(p);
    });

    // EXPMA：指数加权移动平均，alpha = 2/(N+1)
    const expma: Array<number | null> = [];
    const alphaExp = 2 / (expmaPeriod + 1);
    closes.forEach((c, i) => {
      if (i === 0) expma.push(c);
      else expma.push(alphaExp * c + (1 - alphaExp) * (expma[i - 1] ?? c));
    });

    // BOLL 布林带：中轨 = N 日均值，上轨 = 中轨 + K*标准差，下轨 = 中轨 - K*标准差
    const bollMid: Array<number | null> = closes.map((_, i) => {
      if (i < bollN - 1) return null;
      const slice = closes.slice(i - bollN + 1, i + 1);
      return slice.reduce((a, c) => a + c, 0) / bollN;
    });
    const bollStd: Array<number | null> = closes.map((_, i) => {
      if (i < bollN - 1) return null;
      const slice = closes.slice(i - bollN + 1, i + 1);
      const mean = slice.reduce((a, c) => a + c, 0) / bollN;
      const variance = slice.reduce((a, c) => a + (c - mean) ** 2, 0) / bollN;
      return Math.sqrt(variance);
    });
    const bollUp = bollMid.map((m, i) =>
      m == null || bollStd[i] == null ? null : m + bollK * (bollStd[i] as number),
    );
    const bollDn = bollMid.map((m, i) =>
      m == null || bollStd[i] == null ? null : m - bollK * (bollStd[i] as number),
    );

    // MACD：DIF = EMA(fast) - EMA(slow)；DEA = EMA(DIF, signal)；MACD 柱 = (DIF - DEA) * 2
    const ema12: number[] = [];
    const ema26: number[] = [];
    const alpha12 = 2 / (macdFast + 1);
    const alpha26 = 2 / (macdSlow + 1);
    closes.forEach((c, i) => {
      if (i === 0) {
        ema12.push(c);
        ema26.push(c);
      } else {
        ema12.push(alpha12 * c + (1 - alpha12) * ema12[i - 1]);
        ema26.push(alpha26 * c + (1 - alpha26) * ema26[i - 1]);
      }
    });
    const dif = ema12.map((v, i) => v - ema26[i]);
    const dea: number[] = [];
    const alpha9 = 2 / (macdSignal + 1);
    dif.forEach((d, i) => {
      if (i === 0) dea.push(d);
      else dea.push(alpha9 * d + (1 - alpha9) * dea[i - 1]);
    });
    const macd = dif.map((d, i) => (d - dea[i]) * 2);

    // KDJ：RSV = (收 - N日最低) / (N日最高 - N日最低) * 100；K、D 用加权平均迭代
    const k: number[] = [];
    const d: number[] = [];
    const j: number[] = [];
    let kPrev = 50;
    let dPrev = 50;
    closes.forEach((close, i) => {
      const start = Math.max(0, i - (kdjN - 1));
      const hh = Math.max(...highs.slice(start, i + 1));
      const ll = Math.min(...lows.slice(start, i + 1));
      const rsv = hh === ll ? 50 : ((close - ll) / (hh - ll)) * 100;
      const kNow = (2 / 3) * kPrev + (1 / 3) * rsv;
      const dNow = (2 / 3) * dPrev + (1 / 3) * kNow;
      const jNow = 3 * kNow - 2 * dNow;
      k.push(kNow);
      d.push(dNow);
      j.push(jNow);
      kPrev = kNow;
      dPrev = dNow;
    });

    return { maMap, expma, bollUp, bollMid, bollDn, dif, dea, macd, k, d, j };
  }, [bars, maPeriods, expmaPeriod, bollN, bollK, macdFast, macdSlow, macdSignal, kdjN]);

  // 首次加载：获取股票列表，并自动选中 URL 参数指定的标的
  useEffect(() => {
    void (async () => {
      try {
        const rows = await fetchSymbols();
        const code = tsFromUrl?.trim() || "";
        const opts = rows.map((r) => ({
          value: r.ts_code,
          label: r.name ? `${r.ts_code} ${r.name}` : r.ts_code,
        }));
        // 复盘等页面跳转过来时可能带有指数代码（如 000001.SH），
        // 指数不在个股列表里，但仍允许查看其 K 线，所以手动插入下拉选项
        if (code && !rows.some((r) => r.ts_code === code)) {
          opts.unshift({ value: code, label: code });
        }
        setSymbols(opts);
        setTsCode(code || rows[0]?.ts_code);
      } catch (e) {
        setError(getApiErrorMessage(e));
      }
    })();
  }, [tsFromUrl]);

  /**
   * 加载当前标的的 K 线数据
   * 依赖：tsCode、interval、adjType（三者任一变化时重新请求）
   */
  const loadBars = useCallback(async () => {
    if (!tsCode) {
      setBars([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchBars({ ts_code: tsCode, interval, adj: adjType });
      setBars(data);
    } catch (e) {
      setError(getApiErrorMessage(e));
      setBars([]);
    } finally {
      setLoading(false);
    }
  }, [tsCode, interval, adjType]);

  useEffect(() => {
    void loadBars();
  }, [loadBars]);

  // 切换到非日 K 时，自动切换副图回「成交量」（自定义指标只支持日 K）
  useEffect(() => {
    if (interval !== "1d" && subIndicator === "CUSTOM") {
      setSubIndicator("VOL");
      message.warning("自定义副图仅支持日 K，已切换为成交量");
    }
  }, [interval, subIndicator]);

  // 选择「自定义副图」时，加载已保存的自定义 DSL 指标列表
  useEffect(() => {
    if (subIndicator !== "CUSTOM") return;
    void (async () => {
      try {
        const rows = await fetchCustomIndicators();
        setCustomIndicators(rows.filter((r) => r.kind === "dsl" && r.definition));
      } catch {
        setCustomIndicators([]);
      }
    })();
  }, [subIndicator]);

  // 自定义指标列表加载后，自动选中第一个指标（如果还没有选中）
  useEffect(() => {
    if (subIndicator !== "CUSTOM" || !customIndicators.length) return;
    if (customUserIndId != null && customIndicators.some((x) => x.id === customUserIndId)) return;
    const first = customIndicators[0];
    setCustomUserIndId(first.id);
    setCustomSubKey(chartSubKeysForKline(first.definition ?? null)[0]?.value);
  }, [subIndicator, customIndicators, customUserIndId]);

  // 当前选中自定义指标的对象（用于获取其子线配置）
  const selectedCustomInd = useMemo(
    () => customIndicators.find((x) => x.id === customUserIndId),
    [customIndicators, customUserIndId],
  );
  // 当前选中自定义指标可绘制的子线选项列表
  const customSubOptions = useMemo(
    () => chartSubKeysForKline(selectedCustomInd?.definition ?? null),
    [selectedCustomInd],
  );

  // 获取自定义指标的时间序列数据（按选中的标的、子线、复权方式请求）
  useEffect(() => {
    if (subIndicator !== "CUSTOM" || interval !== "1d" || !tsCode || !customUserIndId || !customSubKey) {
      setCustomPoints([]);
      return;
    }
    let cancelled = false; // 用于取消旧请求（切换股票时旧请求结果不应覆盖新请求）
    void (async () => {
      try {
        const s = await fetchCustomIndicatorSeries({
          ts_code: tsCode,
          user_indicator_id: customUserIndId,
          sub_key: customSubKey,
          adj: adjType,
        });
        if (!cancelled) {
          setCustomPoints(s.points);
          setCustomSeriesTitle(`${s.display_name}·${s.sub_key}`);
        }
      } catch (e) {
        if (!cancelled) {
          setCustomPoints([]);
          setError(getApiErrorMessage(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [subIndicator, interval, tsCode, customUserIndId, customSubKey, adjType]);

  // 切换标的或周期时，清空缩放状态（重置到默认显示最近 30%），切换复权时保留
  useEffect(() => {
    dataZoomPreserveRef.current = null;
  }, [tsCode, interval]);

  /**
   * 构建 ECharts 图表配置（option）
   * 每当 bars、指标数据或图表参数变化时重新计算
   *
   * 整体布局：
   * - grid[0]（上方）：K 线蜡烛图 + 主图指标
   * - grid[1]（下方）：副图（成交量 / MACD / KDJ / 自定义）
   * - dataZoom：底部滑块 + 内置鼠标拖拽缩放
   */
  const option = useMemo(() => {
    const zoom = dataZoomPreserveRef.current;
    const dzStart = zoom?.start ?? 70; // 默认显示最后 30% 的数据
    const dzEnd = zoom?.end ?? 100;
    // 左边距随图表宽度自适应，避免 Y 轴标签被截断
    const dynamicLeft = chartWidth < 400 ? 100 : chartWidth < 700 ? 92 : chartWidth < 960 ? 82 : 72;

    const category = bars.map((b) => b.time);
    // ECharts 蜡烛图数据格式：[open, close, low, high]（注意顺序）
    const values = bars.map((b) => [b.open, b.close, b.low, b.high] as number[]);

    // 成交量柱颜色：当日涨（收 >= 昨收）用涨色，跌用跌色（A 股配色）
    const volumes = bars.map((b, i) => {
      const prevClose = i > 0 ? bars[i - 1].close : b.open;
      const isUp = b.close >= prevClose;
      return {
        value: b.volume,
        itemStyle: { color: isUp ? RISE_COLOR : FALL_COLOR },
      };
    });

    return {
      animation: false, // 关闭动画提升大数据量渲染性能
      // 继承暗色主题公共配置（tooltip 样式、文字颜色等）
      ...ECHARTS_BASE_OPTION,
      tooltip: {
        ...ECHARTS_BASE_OPTION.tooltip,
        trigger: "axis",
        axisPointer: { type: "cross" },
        // 自定义 tooltip 显示 OHLCV + 换手率 + 连涨跌统计
        formatter: (params: unknown) => {
          const arr = params as {
            axisValue: string;
            data: number[];
            dataIndex: number;
          }[];
          if (!arr?.length) return "";
          const idx = arr[0].dataIndex;
          const b = bars[idx];
          if (!b) return "";
          const turn =
            b.turnover_rate_avg != null
              ? b.turnover_rate_avg.toFixed(3)
              : "-";
          const limitUpDays = b.consecutive_limit_up_days ?? "-";
          const limitDownDays = b.consecutive_limit_down_days ?? "-";
          const upDays = b.consecutive_up_days ?? "-";
          const downDays = b.consecutive_down_days ?? "-";
          return [
            `<div><b>${b.time}</b></div>`,
            `开: ${b.open}　收: ${b.close}`,
            `高: ${b.high}　低: ${b.low}`,
            `成交量: ${b.volume}`,
            `成交额: ${b.amount}`,
            `换手率(日均): ${turn}`,
            `连涨停: ${limitUpDays}　连跌停: ${limitDownDays}`,
            `连涨: ${upDays}　连跌: ${downDays}`,
          ].join("<br/>");
        },
      },
      grid: [
        // 主图区域：蜡烛图 + 主图指标
        { left: dynamicLeft, right: 24, top: 24, height: 300, containLabel: true },
        // 副图区域：成交量 / MACD / KDJ / 自定义
        { left: dynamicLeft, right: 24, top: 350, height: 110, containLabel: true },
      ],
      dataZoom: [
        {
          // 内置型（鼠标拖拽）：只允许平移，不允许滚轮缩放（避免误操作）
          type: "inside",
          xAxisIndex: [0, 1],
          start: dzStart,
          end: dzEnd,
          zoomOnMouseWheel: false,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
        },
        {
          // 滑块型：在图表底部显示可拖拽的时间范围滑块
          show: true,
          type: "slider",
          xAxisIndex: [0, 1],
          bottom: 8,
          start: dzStart,
          end: dzEnd,
        },
      ],
      xAxis: [
        {
          // 主图 X 轴（时间轴）
          type: "category",
          data: category,
          gridIndex: 0,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false, lineStyle: { color: "#444" } },
          axisTick: { lineStyle: { color: "#444" } },
          axisLabel: { color: "#8c8c8c" },
          splitLine: { show: false },
          min: "dataMin",
          max: "dataMax",
        },
        {
          // 副图 X 轴（隐藏标签，只用于数据对齐）
          type: "category",
          data: category,
          gridIndex: 1,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false, lineStyle: { color: "#444" } },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
          min: "dataMin",
          max: "dataMax",
        },
      ],
      yAxis: [
        {
          // 主图 Y 轴（价格轴）
          scale: true,
          splitArea: { show: true },
          gridIndex: 0,
          axisLine: { lineStyle: { color: "#444" } },
          axisTick: { lineStyle: { color: "#444" } },
          axisLabel: { color: "#8c8c8c" },
          splitLine: { lineStyle: { color: "#2a2a2a" } },
        },
        {
          // 副图 Y 轴（根据副图类型格式化标签）
          scale: true,
          splitNumber: 2,
          gridIndex: 1,
          axisLine: { lineStyle: { color: "#444" } },
          axisTick: { lineStyle: { color: "#444" } },
          axisLabel: {
            color: "#8c8c8c",
            formatter: (value: number) => (subIndicator === "VOL" ? formatVolume(value) : `${Number(value).toFixed(2)}`),
            margin: 14,
          },
          splitLine: { lineStyle: { color: "#2a2a2a" } },
        },
      ],
      // 动态构建所有图表系列（蜡烛图 + 主图指标 + 副图指标）
      series: (() => {
        const arr: unknown[] = [
          {
            // K 线蜡烛图：阳线（涨）用 RISE_COLOR，阴线（跌）用 FALL_COLOR（A 股配色）
            type: "candlestick",
            name: "K线",
            data: values,
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: {
              color: RISE_COLOR,         // 阳线实体填充色（A 股红）
              color0: FALL_COLOR,        // 阴线实体填充色（A 股绿）
              borderColor: RISE_COLOR,   // 阳线边框色
              borderColor0: FALL_COLOR,  // 阴线边框色
            },
          },
        ];

        // 主图指标：MA 均线（用 MA_COLORS 中的颜色依次着色）
        if (mainIndicator === "MA") {
          maPeriods.forEach((p, idx) => {
            arr.push({
              type: "line",
              name: `MA${p}`,
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: indicatorData.maMap[String(p)] ?? [],
              symbol: "none",
              smooth: true,
              lineStyle: { width: 1.5, color: MA_COLORS[idx % MA_COLORS.length] },
            });
          });
        } else if (mainIndicator === "EXPMA") {
          arr.push({
            type: "line",
            name: `EXPMA${expmaPeriod}`,
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: indicatorData.expma,
            symbol: "none",
            smooth: true,
            lineStyle: { width: 1.5, color: MA_COLORS[1] }, // 天蓝色
          });
        } else if (mainIndicator === "BOLL") {
          arr.push(
            {
              type: "line",
              name: "BOLL-UP",
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: indicatorData.bollUp,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ff7875" },
            },
            {
              type: "line",
              name: "BOLL-MID",
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: indicatorData.bollMid,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ffd666" },
            },
            {
              type: "line",
              name: "BOLL-DN",
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: indicatorData.bollDn,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#73d13d" },
            },
          );
        }

        // 副图指标
        if (subIndicator === "VOL") {
          arr.push({
            type: "bar",
            name: "成交量",
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes, // 颜色已在 volumes 数组中按涨跌预设
          });
        } else if (subIndicator === "MACD") {
          arr.push(
            {
              type: "bar",
              name: "MACD",
              xAxisIndex: 1,
              yAxisIndex: 1,
              // MACD 柱：正值（多头趋势）红色，负值（空头趋势）绿色（A 股配色）
              data: indicatorData.macd.map((v) => ({
                value: v,
                itemStyle: { color: v >= 0 ? RISE_COLOR : FALL_COLOR },
              })),
            },
            {
              type: "line",
              name: "DIF",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.dif,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ffd666" }, // 金黄
            },
            {
              type: "line",
              name: "DEA",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.dea,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#69c0ff" }, // 天蓝
            },
          );
        } else if (subIndicator === "KDJ") {
          arr.push(
            {
              type: "line",
              name: "K",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.k,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ffd666" }, // 金黄
            },
            {
              type: "line",
              name: "D",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.d,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#69c0ff" }, // 天蓝
            },
            {
              type: "line",
              name: "J",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.j,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ff7875" }, // 浅红
            },
          );
        } else if (subIndicator === "CUSTOM") {
          arr.push({
            type: "line",
            name: customSeriesTitle,
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: customAligned,
            symbol: "none",
            lineStyle: { width: 1.5, color: "#2563eb" },
          });
        }
        return arr;
      })(),
    };
  }, [bars, chartWidth, indicatorData, mainIndicator, subIndicator, customAligned, customSeriesTitle]);

  /**
   * 将 option 同步到 ECharts 图表实例
   * 同时监听 datazoom 事件，记录用户拖拽后的可视区间
   * 使用 ResizeObserver 让图表随容器宽度自动重绘
   */
  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(el);
      chartInstance.current.on("datazoom", () => {
        const ins = chartInstance.current;
        if (!ins) return;
        const opt = ins.getOption() as { dataZoom?: Array<{ start?: number; end?: number }> };
        const dz = Array.isArray(opt.dataZoom) ? opt.dataZoom[0] : undefined;
        if (dz && typeof dz.start === "number" && typeof dz.end === "number") {
          dataZoomPreserveRef.current = { start: dz.start, end: dz.end };
        }
      });
    }
    chartInstance.current.setOption(option, true);
    setChartWidth(el.clientWidth || 980);
    const ro = new ResizeObserver(() => {
      setChartWidth(el.clientWidth || 980);
      chartInstance.current?.resize();
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
    };
  }, [option]);

  // 组件卸载时销毁 ECharts 实例，释放内存
  useEffect(() => {
    return () => {
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  /**
   * 通过按钮控制图表缩放/平移
   * @param deltaStart - dataZoom start 的变化量（百分比）
   * @param deltaEnd   - dataZoom end 的变化量（百分比）
   */
  const adjustDataZoom = (deltaStart: number, deltaEnd: number) => {
    const ins = chartInstance.current;
    if (!ins) return;
    const opt = ins.getOption();
    const dz = Array.isArray(opt.dataZoom) ? opt.dataZoom[0] : undefined;
    const curStart = Number((dz as { start?: number } | undefined)?.start ?? 70);
    const curEnd = Number((dz as { end?: number } | undefined)?.end ?? 100);
    const nextStart = Math.max(0, Math.min(100, curStart + deltaStart));
    const nextEnd = Math.max(0, Math.min(100, curEnd + deltaEnd));
    if (nextEnd - nextStart < 2) return; // 防止缩放过头导致区间为空
    // 同时更新主图和副图的 dataZoom（两个图需保持 X 轴同步）
    ins.dispatchAction({ type: "dataZoom", start: nextStart, end: nextEnd, dataZoomIndex: 0 });
    ins.dispatchAction({ type: "dataZoom", start: nextStart, end: nextEnd, dataZoomIndex: 1 });
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        K 线
      </Typography.Title>

      {/* 错误提示条：请求失败时显示 */}
      {error ? (
        <Alert type="error" message="请求失败" description={error} showIcon />
      ) : null}

      <Card>
        {/* ── 控制栏：标的选择 + 周期 + 复权 + 指标参数 ──── */}
        <Space wrap style={{ marginBottom: 16 }}>
          {/* 股票/指数搜索下拉，支持按代码或名称模糊搜索 */}
          <Select
            showSearch
            placeholder="选择标的"
            style={{ minWidth: 220 }}
            options={symbols}
            value={tsCode}
            onChange={setTsCode}
            optionFilterProp="label"
          />
          {/* K 线周期：日/周/月/季/年 */}
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={interval}
            onChange={(e) => setInterval(e.target.value as Interval)}
            options={intervals}
          />
          {/* 复权方式 */}
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={adjType}
            onChange={(e) => setAdjType(e.target.value as AdjType)}
            options={[
              { label: "不复权", value: "none" },
              { label: "前复权", value: "qfq" },
              { label: "后复权", value: "hfq" },
            ]}
          />
          {/* 主图指标选择 */}
          <Select
            style={{ minWidth: 160 }}
            value={mainIndicator}
            onChange={(v) => setMainIndicator(v as MainIndicator)}
            options={[
              { value: "none", label: "主图: 无指标" },
              { value: "MA", label: "主图: MA" },
              { value: "EXPMA", label: "主图: EXPMA" },
              { value: "BOLL", label: "主图: BOLL" },
            ]}
          />
          {/* 副图指标选择 */}
          <Select
            style={{ minWidth: 180 }}
            value={subIndicator}
            onChange={(v) => setSubIndicator(v as SubIndicator)}
            options={[
              { value: "VOL", label: "副图: 成交量" },
              { value: "MACD", label: "副图: MACD" },
              { value: "KDJ", label: "副图: KDJ" },
              { value: "CUSTOM", label: "副图: 自定义指标（日K）" },
            ]}
          />

          {/* MA 参数：多选均线周期 */}
          {mainIndicator === "MA" ? (
            <Select
              mode="tags"
              style={{ minWidth: 220 }}
              value={maPeriods.map(String)}
              onChange={(vals) => {
                const parsed = vals
                  .map((v) => Number(v))
                  .filter((v) => Number.isFinite(v) && v >= 2 && v <= 250)
                  .map((v) => Math.round(v));
                const uniq = Array.from(new Set(parsed)).sort((a, b) => a - b);
                if (uniq.length) setMaPeriods(uniq);
              }}
              options={[5, 10, 20, 30, 60, 120, 250].map((v) => ({ value: String(v), label: `MA${v}` }))}
              tokenSeparators={[",", " "]}
              placeholder="MA参数，如 5,10,20"
            />
          ) : null}

          {/* EXPMA 参数 */}
          {mainIndicator === "EXPMA" ? (
            <Space>
              <Typography.Text type="secondary">EXPMA参数</Typography.Text>
              <InputNumber min={2} max={250} size="small" value={expmaPeriod} onChange={(v) => setExpmaPeriod(Number(v) || 12)} />
            </Space>
          ) : null}

          {/* BOLL 参数：N（周期）和 K（标准差倍数） */}
          {mainIndicator === "BOLL" ? (
            <Space>
              <Typography.Text type="secondary">BOLL N</Typography.Text>
              <InputNumber min={5} max={250} size="small" value={bollN} onChange={(v) => setBollN(Number(v) || 20)} />
              <Typography.Text type="secondary">K</Typography.Text>
              <InputNumber min={1} max={5} step={0.5} size="small" value={bollK} onChange={(v) => setBollK(Number(v) || 2)} />
            </Space>
          ) : null}

          {/* MACD 参数：快线/慢线/信号线周期 */}
          {subIndicator === "MACD" ? (
            <Space>
              <Typography.Text type="secondary">MACD</Typography.Text>
              <InputNumber min={2} max={60} size="small" value={macdFast} onChange={(v) => setMacdFast(Number(v) || 12)} />
              <InputNumber min={2} max={120} size="small" value={macdSlow} onChange={(v) => setMacdSlow(Number(v) || 26)} />
              <InputNumber min={2} max={60} size="small" value={macdSignal} onChange={(v) => setMacdSignal(Number(v) || 9)} />
            </Space>
          ) : null}

          {/* KDJ 参数 */}
          {subIndicator === "KDJ" ? (
            <Space>
              <Typography.Text type="secondary">KDJ N</Typography.Text>
              <InputNumber min={5} max={60} size="small" value={kdjN} onChange={(v) => setKdjN(Number(v) || 9)} />
            </Space>
          ) : null}

          {/* 自定义副图参数：选择已保存的 DSL 指标和子线 */}
          {subIndicator === "CUSTOM" && interval === "1d" ? (
            <Space wrap>
              <Typography.Text type="secondary">自定义</Typography.Text>
              <Select
                style={{ minWidth: 200 }}
                placeholder="已保存 DSL 指标"
                value={customUserIndId}
                options={customIndicators.map((r) => ({
                  value: r.id,
                  label: `${r.display_name} (${r.code})`,
                }))}
                onChange={(id) => {
                  setCustomUserIndId(id);
                  const row = customIndicators.find((x) => x.id === id);
                  const sk0 = chartSubKeysForKline(row?.definition ?? null)[0]?.value;
                  setCustomSubKey(sk0);
                }}
              />
              <Select
                style={{ minWidth: 140 }}
                placeholder="子线"
                value={customSubKey}
                options={customSubOptions}
                onChange={setCustomSubKey}
              />
            </Space>
          ) : null}

          {/* 图表导航按钮：平移和缩放 */}
          <Space>
            <Button size="small" onClick={() => adjustDataZoom(-5, -5)}>左移</Button>
            <Button size="small" onClick={() => adjustDataZoom(5, 5)}>右移</Button>
            <Button size="small" onClick={() => adjustDataZoom(3, -3)}>放大</Button>
            <Button size="small" onClick={() => adjustDataZoom(-3, 3)}>缩小</Button>
          </Space>
        </Space>

        {/* ── 图表区域 ──────────────────────────────────────── */}
        <div style={{ position: "relative", minHeight: 480 }}>
          {/* 加载中：显示居中旋转 spinner */}
          {loading ? (
            <Spin
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 2,
              }}
            />
          ) : null}
          {/* ECharts 绘图容器：宽 100%，高固定 520px */}
          <div ref={chartRef} style={{ width: "100%", height: chartHeight }} />
        </div>
      </Card>
    </Space>
  );
}
