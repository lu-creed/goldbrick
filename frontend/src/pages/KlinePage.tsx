import { Alert, Button, Card, InputNumber, Radio, Select, Space, Spin, Typography } from "antd";
import * as echarts from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type AdjType,
  type BarPoint,
  type Interval,
  fetchBars,
  fetchSymbols,
  getApiErrorMessage,
} from "../api/client";

const intervals: { label: string; value: Interval }[] = [
  { label: "日 K", value: "1d" },
  { label: "周 K", value: "1w" },
  { label: "月 K", value: "1M" },
  { label: "季 K", value: "1Q" },
  { label: "年 K", value: "1y" },
];

type MainIndicator = "none" | "MA" | "EXPMA" | "BOLL";
type SubIndicator = "VOL" | "MACD" | "KDJ";

export default function KlinePage() {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  /** 用户拖拽/滑块缩放后的可视区间（百分比）；切换标的或周期时清空，切换复权时保留以便对比同一窗口。 */
  const dataZoomPreserveRef = useRef<{ start: number; end: number } | null>(null);
  const [symbols, setSymbols] = useState<{ label: string; value: string }[]>(
    [],
  );
  const [tsCode, setTsCode] = useState<string | undefined>();
  const [interval, setInterval] = useState<Interval>("1d");
  const [bars, setBars] = useState<BarPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartWidth, setChartWidth] = useState(980);
  const [mainIndicator, setMainIndicator] = useState<MainIndicator>("none");
  const [subIndicator, setSubIndicator] = useState<SubIndicator>("VOL");
  const [maPeriods, setMaPeriods] = useState<number[]>([5, 10, 20]);
  const [expmaPeriod, setExpmaPeriod] = useState<number>(12);
  const [bollN, setBollN] = useState<number>(20);
  const [bollK, setBollK] = useState<number>(2);
  const [macdFast, setMacdFast] = useState<number>(12);
  const [macdSlow, setMacdSlow] = useState<number>(26);
  const [macdSignal, setMacdSignal] = useState<number>(9);
  const [kdjN, setKdjN] = useState<number>(9);
  const [adjType, setAdjType] = useState<AdjType>("none");

  const formatVolume = (v: number): string => {
    const n = Number(v) || 0;
    const abs = Math.abs(n);
    if (abs >= 100000000) return `${(n / 100000000).toFixed(2)}亿`;
    if (abs >= 10000) return `${(n / 10000).toFixed(2)}万`;
    return `${Math.round(n)}`;
  };

  const indicatorData = useMemo(() => {
    const closes = bars.map((b) => b.close);
    const highs = bars.map((b) => b.high);
    const lows = bars.map((b) => b.low);

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

    const expma: Array<number | null> = [];
    const alphaExp = 2 / (expmaPeriod + 1);
    closes.forEach((c, i) => {
      if (i === 0) expma.push(c);
      else expma.push(alphaExp * c + (1 - alphaExp) * (expma[i - 1] ?? c));
    });

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

    return {
      maMap,
      expma,
      bollUp,
      bollMid,
      bollDn,
      dif,
      dea,
      macd,
      k,
      d,
      j,
    };
  }, [bars, maPeriods, expmaPeriod, bollN, bollK, macdFast, macdSlow, macdSignal, kdjN]);

  useEffect(() => {
    void (async () => {
      try {
        const rows = await fetchSymbols();
        setSymbols(
          rows.map((r) => ({
            value: r.ts_code,
            label: r.name ? `${r.ts_code} ${r.name}` : r.ts_code,
          })),
        );
        setTsCode((prev) => prev ?? rows[0]?.ts_code);
      } catch (e) {
        setError(getApiErrorMessage(e));
      }
    })();
  }, []);

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

  useEffect(() => {
    dataZoomPreserveRef.current = null;
  }, [tsCode, interval]);

  const option = useMemo(() => {
    const zoom = dataZoomPreserveRef.current;
    const dzStart = zoom?.start ?? 70;
    const dzEnd = zoom?.end ?? 100;
    const dynamicLeft = chartWidth < 700 ? 92 : chartWidth < 960 ? 82 : 72;
    const category = bars.map((b) => b.time);
    const values = bars.map((b) => [b.open, b.close, b.low, b.high] as number[]);
    const volumes = bars.map((b, i) => {
      const prevClose = i > 0 ? bars[i - 1].close : b.open;
      const isUp = b.close >= prevClose;
      return {
        value: b.volume,
        itemStyle: {
          color: isUp ? "#ef5350" : "#26a69a",
        },
      };
    });
    return {
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
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
        { left: dynamicLeft, right: 24, top: 24, height: 300, containLabel: true },
        { left: dynamicLeft, right: 24, top: 350, height: 110, containLabel: true },
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: [0, 1],
          start: dzStart,
          end: dzEnd,
          zoomOnMouseWheel: false,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
        },
        {
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
          type: "category",
          data: category,
          gridIndex: 0,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false },
          splitLine: { show: false },
          min: "dataMin",
          max: "dataMax",
        },
        {
          type: "category",
          data: category,
          gridIndex: 1,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false },
          axisTick: { show: false },
          axisLabel: { show: false },
          splitLine: { show: false },
          min: "dataMin",
          max: "dataMax",
        },
      ],
      yAxis: [
        { scale: true, splitArea: { show: true }, gridIndex: 0 },
        {
          scale: true,
          splitNumber: 2,
          gridIndex: 1,
          axisLabel: {
            formatter: (value: number) => (subIndicator === "VOL" ? formatVolume(value) : `${Number(value).toFixed(2)}`),
            margin: 14,
          },
        },
      ],
      series: (() => {
        const arr: unknown[] = [
          {
            type: "candlestick",
            name: "K线",
            data: values,
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: {
              color: "#ef5350",
              color0: "#26a69a",
              borderColor: "#ef5350",
              borderColor0: "#26a69a",
            },
          },
        ];

        if (mainIndicator === "MA") {
          const colors = ["#ffd666", "#69c0ff", "#95de64", "#ff85c0", "#b37feb", "#ff9c6e"];
          maPeriods.forEach((p, idx) => {
            arr.push({
              type: "line",
              name: `MA${p}`,
              xAxisIndex: 0,
              yAxisIndex: 0,
              data: indicatorData.maMap[String(p)] ?? [],
              symbol: "none",
              smooth: true,
              lineStyle: { width: 1.5, color: colors[idx % colors.length] },
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
            lineStyle: { width: 1.5, color: "#69c0ff" },
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

        if (subIndicator === "VOL") {
          arr.push({
            type: "bar",
            name: "成交量",
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes,
          });
        } else if (subIndicator === "MACD") {
          arr.push(
            {
              type: "bar",
              name: "MACD",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.macd.map((v) => ({
                value: v,
                itemStyle: { color: v >= 0 ? "#ef5350" : "#26a69a" },
              })),
            },
            {
              type: "line",
              name: "DIF",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.dif,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ffd666" },
            },
            {
              type: "line",
              name: "DEA",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.dea,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#69c0ff" },
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
              lineStyle: { width: 1.2, color: "#ffd666" },
            },
            {
              type: "line",
              name: "D",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.d,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#69c0ff" },
            },
            {
              type: "line",
              name: "J",
              xAxisIndex: 1,
              yAxisIndex: 1,
              data: indicatorData.j,
              symbol: "none",
              lineStyle: { width: 1.2, color: "#ff7875" },
            },
          );
        }
        return arr;
      })(),
    };
  }, [bars, chartWidth, indicatorData, mainIndicator, subIndicator]);

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

  useEffect(() => {
    return () => {
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  const adjustDataZoom = (deltaStart: number, deltaEnd: number) => {
    const ins = chartInstance.current;
    if (!ins) return;
    const opt = ins.getOption();
    const dz = Array.isArray(opt.dataZoom) ? opt.dataZoom[0] : undefined;
    const curStart = Number((dz as { start?: number } | undefined)?.start ?? 70);
    const curEnd = Number((dz as { end?: number } | undefined)?.end ?? 100);
    const nextStart = Math.max(0, Math.min(100, curStart + deltaStart));
    const nextEnd = Math.max(0, Math.min(100, curEnd + deltaEnd));
    if (nextEnd - nextStart < 2) return;
    ins.dispatchAction({
      type: "dataZoom",
      start: nextStart,
      end: nextEnd,
      dataZoomIndex: 0,
    });
    ins.dispatchAction({
      type: "dataZoom",
      start: nextStart,
      end: nextEnd,
      dataZoomIndex: 1,
    });
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        K 线
      </Typography.Title>
      {error ? (
        <Alert type="error" message="请求失败" description={error} showIcon />
      ) : null}
      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <Select
            showSearch
            placeholder="选择标的"
            style={{ minWidth: 220 }}
            options={symbols}
            value={tsCode}
            onChange={setTsCode}
            optionFilterProp="label"
          />
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={interval}
            onChange={(e) => setInterval(e.target.value as Interval)}
            options={intervals}
          />
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
          <Select
            style={{ minWidth: 180 }}
            value={subIndicator}
            onChange={(v) => setSubIndicator(v as SubIndicator)}
            options={[
              { value: "VOL", label: "副图: 成交量" },
              { value: "MACD", label: "副图: MACD" },
              { value: "KDJ", label: "副图: KDJ" },
            ]}
          />
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
          {mainIndicator === "EXPMA" ? (
            <Space>
              <Typography.Text type="secondary">EXPMA参数</Typography.Text>
              <InputNumber min={2} max={250} size="small" value={expmaPeriod} onChange={(v) => setExpmaPeriod(Number(v) || 12)} />
            </Space>
          ) : null}
          {mainIndicator === "BOLL" ? (
            <Space>
              <Typography.Text type="secondary">BOLL N</Typography.Text>
              <InputNumber min={5} max={250} size="small" value={bollN} onChange={(v) => setBollN(Number(v) || 20)} />
              <Typography.Text type="secondary">K</Typography.Text>
              <InputNumber min={1} max={5} step={0.5} size="small" value={bollK} onChange={(v) => setBollK(Number(v) || 2)} />
            </Space>
          ) : null}
          {subIndicator === "MACD" ? (
            <Space>
              <Typography.Text type="secondary">MACD</Typography.Text>
              <InputNumber min={2} max={60} size="small" value={macdFast} onChange={(v) => setMacdFast(Number(v) || 12)} />
              <InputNumber min={2} max={120} size="small" value={macdSlow} onChange={(v) => setMacdSlow(Number(v) || 26)} />
              <InputNumber min={2} max={60} size="small" value={macdSignal} onChange={(v) => setMacdSignal(Number(v) || 9)} />
            </Space>
          ) : null}
          {subIndicator === "KDJ" ? (
            <Space>
              <Typography.Text type="secondary">KDJ N</Typography.Text>
              <InputNumber min={5} max={60} size="small" value={kdjN} onChange={(v) => setKdjN(Number(v) || 9)} />
            </Space>
          ) : null}
          <Space>
            <Button size="small" onClick={() => adjustDataZoom(-5, -5)}>
              左移
            </Button>
            <Button size="small" onClick={() => adjustDataZoom(5, 5)}>
              右移
            </Button>
            <Button size="small" onClick={() => adjustDataZoom(3, -3)}>
              放大
            </Button>
            <Button size="small" onClick={() => adjustDataZoom(-3, 3)}>
              缩小
            </Button>
          </Space>
        </Space>
        <div style={{ position: "relative", minHeight: 480 }}>
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
          <div ref={chartRef} style={{ width: "100%", height: 520 }} />
        </div>
      </Card>
    </Space>
  );
}
