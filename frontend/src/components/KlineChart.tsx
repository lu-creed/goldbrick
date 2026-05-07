/**
 * KlineChart — 使用 TradingView Lightweight Charts v5 渲染 K 线图
 *
 * 结构：
 *   - Pane 0（主图）：蜡烛图 + MA / EXPMA / BOLL 叠加线
 *   - Pane 1（量图）：成交量柱状图
 *   - Pane 2（副图）：MACD / KDJ / 自定义 DSL 指标
 *
 * 父组件（KlinePage）负责所有指标值的计算，本组件只负责渲染。
 */
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { BarPoint } from "../api/client";
import { FALL_COLOR, MA_COLORS, RISE_COLOR } from "../constants/theme";

// ── 类型定义 ─────────────────────────────────────────────────────────────────

/** 由 KlinePage 前端计算好、传入本组件的指标数据 */
export interface IndicatorData {
  maMap: Record<string, Array<number | null>>;
  expma: Array<number | null>;
  bollMid: Array<number | null>;
  bollUp: Array<number | null>;
  bollDn: Array<number | null>;
  dif: number[];
  dea: number[];
  macd: number[];
  k: number[];
  d: number[];
  j: number[];
}

export type MainIndicator = "none" | "MA" | "EXPMA" | "BOLL";
export type SubIndicator  = "VOL" | "MACD" | "KDJ" | "CUSTOM";

export interface KlineChartProps {
  bars: BarPoint[];
  mainIndicator: MainIndicator;
  maPeriods: number[];
  indicatorData: IndicatorData;
  subIndicator: SubIndicator;
  customAligned: Array<number | null>;
  customSeriesTitle: string;
  height?: number;
}

export interface KlineChartHandle {
  scrollLeft: () => void;
  scrollRight: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  fitContent: () => void;
}

// ── 辅助函数 ──────────────────────────────────────────────────────────────────

/** 将 'YYYY-MM-DD' 字符串转为 LWC 接受的时间类型 */
const toTime = (s: string) => s as unknown as UTCTimestamp;

/** 将带 null 的数组转为 LWC 折线数据（跳过 null，形成断点） */
function toLineData(times: string[], values: Array<number | null>) {
  return times
    .map((t, i) => ({ time: toTime(t), value: values[i] }))
    .filter((d) => d.value != null) as { time: UTCTimestamp; value: number }[];
}

// ── 组件 ──────────────────────────────────────────────────────────────────────

const KlineChart = forwardRef<KlineChartHandle, KlineChartProps>(
  (
    {
      bars,
      mainIndicator,
      maPeriods,
      indicatorData,
      subIndicator,
      customAligned,
      customSeriesTitle,
      height = 480,
    },
    ref,
  ) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const tooltipRef   = useRef<HTMLDivElement>(null);
    const chartRef     = useRef<IChartApi | null>(null);

    // ── 暴露给父组件的缩放/平移方法 ──────────────────────────────────────────
    useImperativeHandle(ref, () => ({
      scrollLeft() {
        const ts = chartRef.current?.timeScale();
        const r  = ts?.getVisibleLogicalRange();
        if (!ts || !r) return;
        const shift = (r.to - r.from) * 0.12;
        ts.setVisibleLogicalRange({ from: r.from - shift, to: r.to - shift });
      },
      scrollRight() {
        const ts = chartRef.current?.timeScale();
        const r  = ts?.getVisibleLogicalRange();
        if (!ts || !r) return;
        const shift = (r.to - r.from) * 0.12;
        ts.setVisibleLogicalRange({ from: r.from + shift, to: r.to + shift });
      },
      zoomIn() {
        const ts = chartRef.current?.timeScale();
        const r  = ts?.getVisibleLogicalRange();
        if (!ts || !r) return;
        const diff = (r.to - r.from) * 0.1;
        if (r.to - r.from - 2 * diff < 10) return;
        ts.setVisibleLogicalRange({ from: r.from + diff, to: r.to - diff });
      },
      zoomOut() {
        const ts = chartRef.current?.timeScale();
        const r  = ts?.getVisibleLogicalRange();
        if (!ts || !r) return;
        const diff = (r.to - r.from) * 0.1;
        ts.setVisibleLogicalRange({ from: r.from - diff, to: r.to + diff });
      },
      fitContent() {
        chartRef.current?.timeScale().fitContent();
      },
    }));

    // ── 核心 Effect：创建/更新图表 ───────────────────────────────────────────
    useEffect(() => {
      if (!containerRef.current || bars.length === 0) return;

      // 销毁旧实例（指标类型切换时完整重建）
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }

      const container = containerRef.current;
      const chart = createChart(container, {
        layout: {
          background: { type: ColorType.Solid, color: "#141414" },
          textColor: "#8c8c8c",
        },
        grid: {
          vertLines: { color: "#2d2d2d" },
          horzLines: { color: "#2d2d2d" },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
        },
        rightPriceScale: { borderVisible: false },
        leftPriceScale:  { visible: false },
        timeScale: {
          borderVisible: false,
          rightOffset: 5,
        },
        handleScale: { mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
        width:  container.clientWidth,
        height,
      });
      chartRef.current = chart;

      // ── Pane 大小（主图大 / 量图 / 副图各占一定比例）────────────────────────
      const panes = chart.panes();
      // 第一次只有 pane 0，先加两个 pane
      const volPane = chart.addPane();
      const subPane = chart.addPane();

      // 设置高度比例
      const totalH = height;
      panes[0].setHeight(Math.round(totalH * 0.62));  // 主图
      volPane.setHeight(Math.round(totalH * 0.18));    // 量图
      subPane.setHeight(Math.round(totalH * 0.20));    // 副图

      const times = bars.map((b) => b.time);

      // ── 蜡烛图 ──────────────────────────────────────────────────────────────
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor:        RISE_COLOR,
        downColor:      FALL_COLOR,
        borderUpColor:   RISE_COLOR,
        borderDownColor: FALL_COLOR,
        wickUpColor:     RISE_COLOR,
        wickDownColor:   FALL_COLOR,
      }, 0);
      candleSeries.setData(
        bars.map((b) => ({
          time:  toTime(b.time),
          open:  b.open,
          high:  b.high,
          low:   b.low,
          close: b.close,
        })),
      );

      // ── 主图叠加指标 ────────────────────────────────────────────────────────
      if (mainIndicator === "MA") {
        maPeriods.forEach((p, idx) => {
          const ser: ISeriesApi<"Line"> = chart.addSeries(LineSeries, {
            color:     MA_COLORS[idx % MA_COLORS.length],
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
          }, 0);
          const data = toLineData(times, indicatorData.maMap[String(p)] ?? []);
          ser.setData(data);
        });
      } else if (mainIndicator === "EXPMA") {
        const ser = chart.addSeries(LineSeries, {
          color: MA_COLORS[1], lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false,
        }, 0);
        ser.setData(toLineData(times, indicatorData.expma));
      } else if (mainIndicator === "BOLL") {
        const bollOpts = {
          lineWidth: 1 as const,
          priceLineVisible: false,
          lastValueVisible: false,
        };
        const upper = chart.addSeries(LineSeries, { ...bollOpts, color: "#ff7875" }, 0);
        const mid   = chart.addSeries(LineSeries, { ...bollOpts, color: "#69c0ff" }, 0);
        const lower = chart.addSeries(LineSeries, { ...bollOpts, color: "#95de64" }, 0);
        upper.setData(toLineData(times, indicatorData.bollUp));
        mid.setData(toLineData(times, indicatorData.bollMid));
        lower.setData(toLineData(times, indicatorData.bollDn));
      }

      // ── 成交量柱状图（pane 1）────────────────────────────────────────────────
      const volSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: "vol",
        priceLineVisible: false,
        lastValueVisible: false,
      }, 1);
      volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0 } });
      volSeries.setData(
        bars.map((b, i) => ({
          time:  toTime(b.time),
          value: b.volume,
          color: i > 0 && b.close >= bars[i - 1].close ? `${RISE_COLOR}aa` : `${FALL_COLOR}aa`,
        })),
      );

      // ── 副图（pane 2）───────────────────────────────────────────────────────
      if (subIndicator === "MACD") {
        const { dif, dea, macd } = indicatorData;
        const macdBar = chart.addSeries(HistogramSeries, {
          priceLineVisible: false, lastValueVisible: false,
          priceScaleId: "macd",
        }, 2);
        macdBar.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
        macdBar.setData(
          times.map((t, i) => ({
            time:  toTime(t),
            value: macd[i] ?? 0,
            color: (macd[i] ?? 0) >= 0 ? `${RISE_COLOR}cc` : `${FALL_COLOR}cc`,
          })),
        );
        const difSer = chart.addSeries(LineSeries, {
          color: "#ffd666", lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, priceScaleId: "macd",
        }, 2);
        difSer.setData(times.map((t, i) => ({ time: toTime(t), value: dif[i] ?? 0 })));

        const deaSer = chart.addSeries(LineSeries, {
          color: "#69c0ff", lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, priceScaleId: "macd",
        }, 2);
        deaSer.setData(times.map((t, i) => ({ time: toTime(t), value: dea[i] ?? 0 })));

      } else if (subIndicator === "KDJ") {
        const { k, d, j } = indicatorData;
        const kColors = ["#ffd666", "#69c0ff", "#ff85c0"];
        [k, d, j].forEach((arr, idx) => {
          const ser = chart.addSeries(LineSeries, {
            color: kColors[idx], lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false,
          }, 2);
          ser.setData(times.map((t, i) => ({ time: toTime(t), value: arr[i] ?? 50 })));
        });

      } else if (subIndicator === "CUSTOM" && customAligned.length > 0) {
        const ser = chart.addSeries(LineSeries, {
          color: MA_COLORS[0], lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false,
          title: customSeriesTitle,
        }, 2);
        ser.setData(toLineData(times, customAligned));

      } else {
        // VOL 模式只有量图，副图 pane 为空，缩到最小高度
        subPane.setHeight(0);
        // 把量图撑满剩余空间
        volPane.setHeight(Math.round(totalH * 0.35));
        panes[0].setHeight(Math.round(totalH * 0.65));
      }

      // ── Tooltip ─────────────────────────────────────────────────────────────
      const tooltip = tooltipRef.current;
      chart.subscribeCrosshairMove((param) => {
        if (!tooltip) return;
        if (!param.point || !param.time) {
          tooltip.style.display = "none";
          return;
        }
        const timeStr = param.time as string;
        const idx = bars.findIndex((b) => b.time === timeStr);
        if (idx < 0) { tooltip.style.display = "none"; return; }
        const b = bars[idx];

        const prevClose = idx > 0 ? bars[idx - 1].close : b.open;
        const chg = b.close - prevClose;
        const chgPct = prevClose ? (chg / prevClose * 100) : 0;
        const chgStr = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)} (${chgPct >= 0 ? "+" : ""}${chgPct.toFixed(2)}%)`;
        const turn = b.turnover_rate_avg != null ? `${b.turnover_rate_avg.toFixed(3)}%` : "-";

        tooltip.innerHTML = [
          `<b>${b.time}</b>`,
          `开 ${b.open.toFixed(2)}  收 ${b.close.toFixed(2)}`,
          `高 ${b.high.toFixed(2)}  低 ${b.low.toFixed(2)}`,
          `涨跌: ${chgStr}`,
          `换手率: ${turn}`,
          b.consecutive_limit_up_days ? `连板 ${b.consecutive_limit_up_days}` : "",
        ].filter(Boolean).join("<br/>");
        tooltip.style.display = "block";

        // 根据鼠标位置决定 tooltip 左/右显示，避免超出边界
        const chartW = container.clientWidth;
        const x = param.point.x;
        if (x < chartW / 2) {
          tooltip.style.left = `${x + 12}px`;
          tooltip.style.right = "auto";
        } else {
          tooltip.style.right = `${chartW - x + 12}px`;
          tooltip.style.left = "auto";
        }
        tooltip.style.top = "8px";
      });

      // ── 初始视口：默认显示最后 ~35% 的数据 ─────────────────────────────────
      const total = bars.length;
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, total * 0.65),
        to:   total + 1,
      });

      // ── ResizeObserver 自适应容器宽度 ───────────────────────────────────────
      const ro = new ResizeObserver(() => {
        chart.applyOptions({ width: container.clientWidth });
      });
      ro.observe(container);

      return () => {
        ro.disconnect();
        chart.remove();
        chartRef.current = null;
      };
    }, [
      bars,
      mainIndicator,
      maPeriods,
      subIndicator,
      indicatorData,
      customAligned,
      customSeriesTitle,
      height,
    ]);

    return (
      <div style={{ position: "relative" }}>
        <div ref={containerRef} style={{ width: "100%", height }} />
        {/* Tooltip 浮层 */}
        <div
          ref={tooltipRef}
          style={{
            position:     "absolute",
            display:      "none",
            background:   "rgba(20,20,20,0.88)",
            color:        "#e0e0e0",
            padding:      "6px 10px",
            borderRadius: 4,
            fontSize:     12,
            lineHeight:   "1.6",
            pointerEvents: "none",
            border:       "1px solid #444",
            zIndex:       10,
            maxWidth:     220,
          }}
        />
      </div>
    );
  },
);

KlineChart.displayName = "KlineChart";
export default KlineChart;
