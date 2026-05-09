/**
 * 股票复盘页面
 *
 * 功能：展示某一交易日的市场整体情绪快照，包括：
 * - 三大股指（上证、深证、创业板）当日涨跌情况
 * - 全市场涨跌家数统计
 * - 涨跌停家数统计
 * - 涨跌幅分布直方图（每个区间有多少只股票）
 *
 * 注意：期间复盘（多日趋势）暂未实现，按钮已禁用。
 */
import { Card, Col, DatePicker, Divider, Row, Skeleton, Space, Statistic, Tabs, Typography, message, theme } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import * as echarts from "echarts";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { type ReplayDailyOut, fetchReplayDaily, getApiErrorMessage } from "../api/client";
import { ECHARTS_BASE_OPTION, FALL_COLOR, FLAT_COLOR, RISE_COLOR } from "../constants/theme";
import { useIsMobile } from "../hooks/useIsMobile";

const { Text } = Typography;

/**
 * 把成交金额格式化成"X 亿"或"X 万"，方便人眼阅读
 * @param v - 原始数字（单位：元）
 * @returns 格式化后的字符串，无数据时返回 "—"
 */
function formatAmount(v: number): string {
  if (!v || v <= 0) return "—";
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
}

/**
 * 根据指数当日涨跌幅，决定指数卡片的背景颜色
 * - 上涨：淡红色（A 股涨色）
 * - 下跌：淡绿色（A 股跌色）
 * - 平盘或无数据：中性深灰色
 *
 * @param dataOk      - 该指数是否有有效数据
 * @param pct         - 涨跌幅（百分比），如 1.23 表示涨 1.23%
 * @param neutralFill - 无数据时的默认背景色
 */
function indexCardBackground(
  dataOk: boolean,
  pct: number | null | undefined,
  neutralFill: string,
): string {
  if (!dataOk) return neutralFill;
  if (pct == null || Math.abs(pct) < 1e-9) return "rgba(15, 23, 42, 0.14)"; // 平盘：近黑
  if (pct > 0) return "rgba(245, 34, 45, 0.12)";  // 涨：淡红（A 股红）
  return "rgba(82, 196, 26, 0.12)";               // 跌：淡绿（A 股绿）
}

export default function ReplayPage() {
  // token 用于读取 Ant Design 当前主题颜色，确保卡片边框等颜色与主题一致
  const { token } = theme.useToken();
  const isMobile = useIsMobile();
  // navigate 用于点击指数卡片后跳转到 K 线页
  const navigate = useNavigate();

  // bucketRef：指向涨跌幅分布直方图容器 DOM 元素
  const bucketRef = useRef<HTMLDivElement>(null);
  // bucketChart：保存 ECharts 图表实例，避免重复初始化
  const bucketChart = useRef<echarts.ECharts | null>(null);

  // scatterRef / scatterChart：换手率-涨跌幅散点图
  const scatterRef   = useRef<HTMLDivElement>(null);
  const scatterChart = useRef<echarts.ECharts | null>(null);
  // navigateRef：把 navigate 存入 ref，让 ECharts click 回调能用到最新的函数引用
  // （ECharts 事件回调是在 setOption 时注册的闭包，不能直接依赖 React hook）
  const navigateRef = useRef(navigate);
  useEffect(() => { navigateRef.current = navigate; }, [navigate]);

  // loading：是否正在请求数据（控制骨架屏显示）
  const [loading, setLoading] = useState(false);
  // data：从后端获取的复盘数据（null 表示尚未加载完成）
  const [data, setData] = useState<ReplayDailyOut | null>(null);
  // picked：用户在日期选择器中选中的日期
  const [picked, setPicked] = useState<Dayjs | null>(null);

  /**
   * 加载指定日期的复盘数据
   * @param d - 日期字符串，格式 "YYYY-MM-DD"；不传则加载最新交易日
   */
  const load = useCallback(async (d: string | undefined) => {
    setLoading(true);
    try {
      // list_limit: 400 表示最多返回 400 只股票的明细（用于分布统计）
      const out = await fetchReplayDaily(
        d ? { trade_date: d, list_limit: 2000 } : { list_limit: 2000 },
      );
      setData(out);
      // 如果用户还没有手动选日期，则自动把日期选择器定位到后端返回的交易日
      setPicked((prev) => prev ?? (out.trade_date ? dayjs(out.trade_date) : null));
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // 组件挂载时加载最新交易日数据（不传日期参数）
  useEffect(() => {
    void load(undefined);
  }, []);

  /**
   * 当复盘数据到达后，绘制涨跌幅分布直方图
   * 每次 data.buckets 变化（切换日期）都会重新调用 setOption 更新图表
   */
  useEffect(() => {
    if (!data?.buckets?.length || !bucketRef.current) return;
    // 只初始化一次 ECharts 实例；后续复用同一个实例
    if (!bucketChart.current) bucketChart.current = echarts.init(bucketRef.current);

    const labels = data.buckets.map((b) => b.label); // 区间名，如 "-10%~-7%"
    const vals = data.buckets.map((b) => b.count);   // 每个区间的股票数量

    bucketChart.current.setOption({
      // 继承暗色主题公共配置（背景、文字颜色、tooltip 样式等）
      ...ECHARTS_BASE_OPTION,
      grid: { left: 48, right: 16, top: 36, bottom: 72 },
      xAxis: {
        ...ECHARTS_BASE_OPTION.xAxis,
        type: "category",
        data: labels,
        axisLabel: { rotate: 35, fontSize: 10, color: "#8c8c8c" },
      },
      yAxis: {
        ...ECHARTS_BASE_OPTION.yAxis,
        type: "value",
        name: "家数",
      },
      series: [
        {
          type: "bar",
          data: vals,
          // 根据涨跌区间着色：正值区间（涨）用红色，负值区间（跌）用绿色
          itemStyle: {
            color: (params: { dataIndex: number }) => {
              const label = labels[params.dataIndex];
              if (label?.startsWith("+") || label?.includes("涨停")) return RISE_COLOR;
              if (label?.startsWith("-") || label?.includes("跌停")) return FALL_COLOR;
              return FLAT_COLOR;
            },
            borderRadius: [4, 4, 0, 0], // 柱子顶部圆角
          },
          // 柱顶展示该桶内的股票家数，便于直接读数
          label: { show: true, position: "top", formatter: "{c}", color: "#8c8c8c", fontSize: 10 },
        },
      ],
    });

    // ResizeObserver：当容器大小变化时自动重绘图表（适应窗口缩放）
    const ro = new ResizeObserver(() => bucketChart.current?.resize());
    ro.observe(bucketRef.current);
    return () => ro.disconnect(); // 组件销毁时取消监听，防止内存泄漏
  }, [data?.buckets]);

  /**
   * 绘制换手率-涨跌幅散点图
   * X 轴：换手率（%），Y 轴：涨跌幅（%），颜色按涨跌着色
   * 每次 data.stocks 变化（切换日期）重新绘制
   */
  useEffect(() => {
    if (!data?.stocks?.length || !scatterRef.current) return;
    if (!scatterChart.current) scatterChart.current = echarts.init(scatterRef.current);

    // 过滤掉没有换手率的股票（科创板/北交所等少量股票可能缺失）
    const stocks = data.stocks.filter((s) => s.turnover_rate != null);

    // 按涨跌拆分三组数据，分组后可以在图例中分别显示并着色
    const riseData = stocks
      .filter((s) => s.pct_change > 0)
      .map((s) => ({ value: [s.turnover_rate, s.pct_change], name: `${s.ts_code} ${s.name ?? ""}` }));
    const fallData = stocks
      .filter((s) => s.pct_change < 0)
      .map((s) => ({ value: [s.turnover_rate, s.pct_change], name: `${s.ts_code} ${s.name ?? ""}` }));
    const flatData = stocks
      .filter((s) => s.pct_change === 0)
      .map((s) => ({ value: [s.turnover_rate, s.pct_change], name: `${s.ts_code} ${s.name ?? ""}` }));

    scatterChart.current.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      legend: {
        data: ["上涨", "下跌", "平盘"],
        top: 8,
        textStyle: { color: "#d9d9d9", fontSize: 12 },
      },
      grid: { left: 56, right: 20, top: 48, bottom: 56 },
      tooltip: {
        trigger: "item",
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          const [turnover, pct] = params.value as [number, number];
          const sign = pct > 0 ? "+" : "";
          return `${params.name}<br/>换手率 ${turnover.toFixed(2)}%<br/>涨跌幅 ${sign}${pct.toFixed(2)}%<br/><span style="color:#8c8c8c;font-size:11px">点击查看 K 线</span>`;
        },
      },
      xAxis: {
        ...ECHARTS_BASE_OPTION.xAxis,
        type: "value",
        name: "换手率 %",
        nameTextStyle: { color: "#8c8c8c", fontSize: 11 },
        nameLocation: "middle",
        nameGap: 28,
        axisLabel: { color: "#8c8c8c", formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: "#1e1e1e" } },
      },
      yAxis: {
        ...ECHARTS_BASE_OPTION.yAxis,
        type: "value",
        name: "涨跌幅 %",
        nameTextStyle: { color: "#8c8c8c", fontSize: 11 },
        nameLocation: "middle",
        nameGap: 40,
        axisLabel: { color: "#8c8c8c", formatter: (v: number) => `${v > 0 ? "+" : ""}${v}%` },
        splitLine: { lineStyle: { color: "#1e1e1e" } },
        // Y=0 基准线：区分涨跌的分界
        markLine: {
          silent: true,
          symbol: ["none", "none"],
          data: [{ yAxis: 0, lineStyle: { color: "#444", type: "dashed", width: 1 } }],
        },
      },
      series: [
        {
          name: "上涨",
          type: "scatter",
          data: riseData,
          symbolSize: 4,
          itemStyle: { color: RISE_COLOR, opacity: 0.6 },
        },
        {
          name: "下跌",
          type: "scatter",
          data: fallData,
          symbolSize: 4,
          itemStyle: { color: FALL_COLOR, opacity: 0.6 },
        },
        {
          name: "平盘",
          type: "scatter",
          data: flatData,
          symbolSize: 4,
          itemStyle: { color: FLAT_COLOR, opacity: 0.6 },
        },
      ],
    });

    const ro = new ResizeObserver(() => scatterChart.current?.resize());
    ro.observe(scatterRef.current);

    // 点击散点：跳转到对应股票的 K 线页
    // params.name 格式为 "000001.SZ 平安银行"，取第一段即 ts_code
    const chart = scatterChart.current;
    chart.off("click"); // 先解绑旧监听，防止切换日期后重复注册
    chart.on("click", (params: { name?: string }) => {
      if (!params.name) return;
      const tsCode = params.name.split(" ")[0];
      if (tsCode) navigateRef.current(`/?ts_code=${encodeURIComponent(tsCode)}`);
    });

    return () => ro.disconnect();
  }, [data?.stocks]);

  /** 用户手动切换日期时触发，重新加载对应交易日数据 */
  const onDateChange = (v: Dayjs | null) => {
    setPicked(v);
    if (v) void load(v.format("YYYY-MM-DD"));
  };

  return (
    <div style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        股票复盘
      </Typography.Title>

      {/* 单日复盘 / 期间复盘 切换标签（期间复盘暂未实现，禁用） */}
      <Tabs
        items={[
          { key: "day", label: "单日复盘" },
          { key: "range", label: "期间复盘", disabled: true },
        ]}
      />

      {/* ── 主数据卡片：日期选择 + 三大股指 ─────────────────── */}
      <Card
        style={{ borderRadius: 12, borderColor: token.colorBorderSecondary, marginBottom: 16 }}
        styles={{ body: { padding: 24 } }}
      >
        {/* 骨架屏：数据加载中时显示占位符，避免空白闪烁 */}
        {loading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <>
            <Space wrap style={{ marginBottom: 16 }}>
              <Text type="secondary">交易日</Text>
              <DatePicker value={picked} onChange={onDateChange} allowClear={false} />
              {data?.latest_bar_date && (
                <Text type="secondary">本地最新日线：{data.latest_bar_date}</Text>
              )}
            </Space>

            {/* 三大股指卡片：点击可跳转到该指数 K 线页 */}
            <Row gutter={[16, 16]}>
              {data?.indices.map((ix) => (
                <Col xs={24} sm={8} key={ix.ts_code}>
                  <div
                    role="button"
                    tabIndex={0}
                    title={ix.data_ok ? "点击查看该指数 K 线" : undefined}
                    onClick={() => {
                      if (ix.data_ok) navigate(`/?ts_code=${encodeURIComponent(ix.ts_code)}`);
                    }}
                    onKeyDown={(e) => {
                      if (!ix.data_ok) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/?ts_code=${encodeURIComponent(ix.ts_code)}`);
                      }
                    }}
                    style={{
                      borderRadius: 12,
                      padding: 16,
                      border: `1px solid ${token.colorBorderSecondary}`,
                      background: indexCardBackground(ix.data_ok, ix.pct_change, token.colorFillAlter),
                      cursor: ix.data_ok ? "pointer" : "default",
                    }}
                  >
                    <Text strong>{ix.name}</Text>
                    {!ix.data_ok ? (
                      <Text type="danger" style={{ display: "block", marginTop: 8, fontSize: 12 }}>
                        {ix.message ?? "无数据"}
                      </Text>
                    ) : (
                      <>
                        <Statistic
                          value={ix.close}
                          precision={2}
                          valueStyle={{ fontSize: 22 }}
                          suffix={
                            ix.pct_change != null ? (
                              <span
                                style={{
                                  fontSize: 14,
                                  marginLeft: 8,
                                  // A 股配色：涨红跌绿
                                  color:
                                    ix.pct_change > 0
                                      ? RISE_COLOR
                                      : ix.pct_change < 0
                                        ? FALL_COLOR
                                        : FLAT_COLOR,
                                }}
                              >
                                {ix.pct_change >= 0 ? "+" : ""}
                                {ix.pct_change.toFixed(2)}%
                              </span>
                            ) : null
                          }
                        />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          成交额 {formatAmount(ix.amount)}
                        </Text>
                      </>
                    )}
                  </div>
                </Col>
              ))}
            </Row>
          </>
        )}
      </Card>

      {/* ── 统计卡片（合并涨跌家数 + 涨跌停家数）──────────── */}
      <Card style={{ borderRadius: 12 }}>
        {loading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : (
          <Row justify="space-around" align="middle" wrap>
            <Col><Statistic title="上涨" value={data?.up_count ?? 0} valueStyle={{ color: RISE_COLOR }} /></Col>
            <Col><Statistic title="平盘" value={data?.flat_count ?? 0} valueStyle={{ color: FLAT_COLOR }} /></Col>
            <Col><Statistic title="下跌" value={data?.down_count ?? 0} valueStyle={{ color: FALL_COLOR }} /></Col>
            <Divider type="vertical" style={{ height: 48, margin: "0 8px" }} />
            <Col><Statistic title="涨停" value={data?.limit_up_count ?? 0} valueStyle={{ color: RISE_COLOR }} /></Col>
            <Col><Statistic title="跌停" value={data?.limit_down_count ?? 0} valueStyle={{ color: FALL_COLOR }} /></Col>
          </Row>
        )}
      </Card>

      {/* ── 涨跌幅分布 + 换手率散点图（并排）──────────────── */}
      <Row gutter={[16, 16]} style={{ marginTop: 8 }}>
        <Col xs={24} lg={12}>
          <Card title="涨跌幅分布" style={{ borderRadius: 12 }}>
            {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : (
              <div ref={bucketRef} style={{ height: isMobile ? 240 : 320 }} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title="换手率 · 涨跌幅散点图"
            extra={
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {data?.stocks?.filter((s) => s.turnover_rate != null).length ?? 0} 只（含换手率数据）
              </Typography.Text>
            }
            style={{ borderRadius: 12 }}
          >
            {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : (
              <div ref={scatterRef} style={{ height: isMobile ? 300 : 320 }} />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
