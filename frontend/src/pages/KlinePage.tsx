/**
 * K 线页面
 *
 * 这是整个应用的核心查看页面，功能包括：
 * - 选择股票标的、K 线周期（日/周/月/季/年）、复权方式（不复权/前复权/后复权）
 * - 主图叠加均线指标：MA、EXPMA、BOLL
 * - 副图切换：成交量、MACD、KDJ、自定义 DSL 指标
 * - 图表缩放/平移（鼠标拖拽 + 按钮）
 * - 鼠标悬停时弹出 tooltip 显示 OHLC 及换手率等信息
 *
 * 技术实现：TradingView Lightweight Charts v5 + 所有指标均前端计算
 */
import { Alert, Button, Card, InputNumber, Radio, Select, Space, Spin, Tag, Typography, message } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  type AdjType,
  type BarPoint,
  type FundamentalSnapshot,
  type Interval,
  type UserIndicatorOut,
  fetchBars,
  fetchCustomIndicatorSeries,
  fetchCustomIndicators,
  fetchFundamentalSnapshot,
  fetchSymbols,
  getApiErrorMessage,
} from "../api/client";
import KlineChart, { type KlineChartHandle } from "../components/KlineChart";
import StockFundamentalsPanel from "../components/StockFundamentalsPanel";
import { useIsMobile } from "../hooks/useIsMobile";
import { useAuth } from "../hooks/useAuth";

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
  const { isGuest, openLoginGate } = useAuth();

  // klineChartRef：指向 KlineChart 实例，用于按钮控制缩放/平移
  const klineChartRef = useRef<KlineChartHandle>(null);

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
  const isMobile = useIsMobile();
  const chartHeight = isMobile ? 420 : 620;

  // 财务快照
  const [fundamentals, setFundamentals] = useState<FundamentalSnapshot | null>(null);
  const [fundLoading, setFundLoading] = useState(false);

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

  // 标的切换时加载财务快照（独立于 K 线加载，不阻塞图表）
  useEffect(() => {
    if (!tsCode) { setFundamentals(null); return; }
    setFundLoading(true);
    fetchFundamentalSnapshot(tsCode)
      .then((snap) => setFundamentals(snap))
      .catch(() => setFundamentals(null))
      .finally(() => setFundLoading(false));
  }, [tsCode]);

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

  /** 通过 KlineChart 实例控制图表缩放/平移 */
  const adjustDataZoom = (type: "left" | "right" | "in" | "out") => {
    switch (type) {
      case "left":  klineChartRef.current?.scrollLeft(); break;
      case "right": klineChartRef.current?.scrollRight(); break;
      case "in":    klineChartRef.current?.zoomIn(); break;
      case "out":   klineChartRef.current?.zoomOut(); break;
    }
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
            onChange={(v) => {
              // 访客软挡:「自定义指标」依赖用户私有 DSL,访客无 → 弹登录 Modal,
              // 成功后回调自动切换到 CUSTOM 选项
              if (v === "CUSTOM" && isGuest) {
                openLoginGate({
                  message: "登录后可以在副图叠加你自己的 DSL 自定义指标子线",
                  onSuccess: () => setSubIndicator("CUSTOM"),
                });
                return;
              }
              setSubIndicator(v as SubIndicator);
            }}
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
            <Button size="small" onClick={() => adjustDataZoom("left")}>左移</Button>
            <Button size="small" onClick={() => adjustDataZoom("right")}>右移</Button>
            <Button size="small" onClick={() => adjustDataZoom("in")}>放大</Button>
            <Button size="small" onClick={() => adjustDataZoom("out")}>缩小</Button>
          </Space>
        </Space>

        {/* 副图口径说明小 Tag:让用户明确副图当前显示的指标 × 复权口径,
            避免「副图数值看上去不对」其实是复权切换导致的误解 */}
        <Tag style={{ marginBottom: 6, fontSize: 11 }}>
          副图:{subIndicator === "CUSTOM" ? "自定义指标" : subIndicator} · {adjType === "qfq" ? "前复权" : adjType === "hfq" ? "后复权" : "不复权"}
        </Tag>

        {/* ── 图表区域 ──────────────────────────────────────── */}
        <div style={{ position: "relative", minHeight: chartHeight }}>
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
          {/* TradingView Lightweight Charts K 线图 */}
          <KlineChart
            ref={klineChartRef}
            bars={bars}
            mainIndicator={mainIndicator}
            maPeriods={maPeriods}
            indicatorData={indicatorData}
            subIndicator={subIndicator}
            customAligned={customAligned}
            customSeriesTitle={customSeriesTitle}
            height={chartHeight}
          />
        </div>
      </Card>
      <StockFundamentalsPanel snapshot={fundamentals} loading={fundLoading} />
    </Space>
  );
}
