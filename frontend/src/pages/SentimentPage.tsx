/**
 * 大V情绪仪表盘
 *
 * 聚合视角：综合呈现近60个交易日市场情绪的量化走势，
 * 辅助判断市场整体冷热，是追涨还是谨慎。
 *
 * 核心指标：
 * - 情绪分（0~100）：综合涨跌家数与涨停热度的合成指数
 * - 涨停数量趋势：反映热钱活跃度，涨停潮通常伴随情绪高峰
 * - 上涨家数比例：反映市场普涨/普跌结构
 * - 涨跌家数柱状图：每日结构分布，直观看强弱切换
 */
import * as echarts from "echarts";
import {
  Card,
  Col,
  Radio,
  Row,
  Skeleton,
  Space,
  Statistic,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { InfoCircleOutlined, FireOutlined, FundOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSentimentTrend, getApiErrorMessage, type SentimentTrendPoint } from "../api/client";
import { ECHARTS_BASE_OPTION, RISE_COLOR } from "../constants/theme";
import { useIsMobile } from "../hooks/useIsMobile";

const { Title, Text } = Typography;

/** 情绪分对应的文字标签和颜色 */
function sentimentLabel(score: number): { text: string; color: string } {
  if (score >= 75) return { text: "极度乐观", color: "#ff4d4f" };
  if (score >= 60) return { text: "偏乐观",   color: "#fa8c16" };
  if (score >= 45) return { text: "中性",     color: "#8c8c8c" };
  if (score >= 30) return { text: "偏悲观",   color: "#1677ff" };
  return               { text: "极度悲观",   color: "#531dab" };
}

export default function SentimentPage() {
  const [days, setDays] = useState(60);
  const [loading, setLoading] = useState(false);
  const [points, setPoints] = useState<SentimentTrendPoint[]>([]);
  const isMobile = useIsMobile();

  // 三个图表的容器 ref
  const scoreChartRef  = useRef<HTMLDivElement>(null);
  const limitChartRef  = useRef<HTMLDivElement>(null);
  const adChartRef     = useRef<HTMLDivElement>(null);
  const scoreChart     = useRef<echarts.ECharts | null>(null);
  const limitChart     = useRef<echarts.ECharts | null>(null);
  const adChart        = useRef<echarts.ECharts | null>(null);

  const loadData = useCallback(async (d: number) => {
    setLoading(true);
    try {
      const res = await fetchSentimentTrend({ days: d });
      setPoints(res.points);
    } catch (e) {
      message.error(getApiErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadData(days); }, [days, loadData]);

  // ---- 绘制情绪分趋势折线 ----
  useEffect(() => {
    if (points.length === 0 || !scoreChartRef.current) return;
    if (!scoreChart.current) scoreChart.current = echarts.init(scoreChartRef.current);
    const chart = scoreChart.current;
    const dates  = points.map((p) => p.trade_date);
    const scores = points.map((p) => p.sentiment_score);

    // 区域根据情绪分高低染色：≥60 红暖色，≤40 冷蓝色
    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        formatter: (params: echarts.TooltipComponentFormatterCallbackParams) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          const p = params[0];
          const score = Number(p.value);
          const { text, color } = sentimentLabel(score);
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const dateStr = String((p as any).axisValue ?? p.name ?? "");
          return `${dateStr}<br/>情绪分：<b style="color:${color}">${score.toFixed(1)}</b>（${text}）`;
        },
      },
      grid: { left: 50, right: 20, top: 30, bottom: 40 },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      yAxis: {
        type: "value", min: 0, max: 100,
        axisLabel: { color: "#8c8c8c" },
        splitLine: { lineStyle: { color: "#222" } },
        // 参考线：超买 70 / 超卖 30
        splitNumber: 5,
      },
      visualMap: {
        show: false,
        type: "continuous",
        min: 0, max: 100,
        inRange: {
          color: ["#1677ff", "#8c8c8c", "#ff4d4f"],
        },
      },
      series: [{
        name: "情绪分",
        type: "line",
        data: scores,
        smooth: true,
        symbol: "none",
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.15 },
        markLine: {
          silent: true,
          lineStyle: { type: "dashed", color: "#555" },
          data: [
            { yAxis: 70, label: { formatter: "乐观 70", color: "#fa8c16" } },
            { yAxis: 30, label: { formatter: "悲观 30", color: "#1677ff" } },
          ],
        },
      }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [points]);

  // ---- 绘制涨停数量 + 跌停数量折线 ----
  useEffect(() => {
    if (points.length === 0 || !limitChartRef.current) return;
    if (!limitChart.current) limitChart.current = echarts.init(limitChartRef.current);
    const chart = limitChart.current;
    const dates = points.map((p) => p.trade_date);
    const lus   = points.map((p) => p.limit_up_count);
    const lds   = points.map((p) => p.limit_down_count);

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
      },
      legend: { data: ["涨停数", "跌停数"], top: 4, textStyle: { color: "#d9d9d9", fontSize: 11 } },
      grid: { left: 50, right: 20, top: 36, bottom: 40 },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      yAxis: { type: "value", axisLabel: { color: "#8c8c8c" }, splitLine: { lineStyle: { color: "#222" } } },
      series: [
        {
          name: "涨停数",
          type: "line",
          data: lus,
          smooth: true,
          symbol: "none",
          lineStyle: { color: RISE_COLOR, width: 2 },
          areaStyle: { color: "rgba(255,77,79,0.08)" },
          itemStyle: { color: RISE_COLOR },
        },
        {
          name: "跌停数",
          type: "line",
          data: lds,
          smooth: true,
          symbol: "none",
          lineStyle: { color: "#1677ff", width: 1.5 },
          areaStyle: { color: "rgba(22,119,255,0.06)" },
          itemStyle: { color: "#1677ff" },
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [points]);

  // ---- 绘制涨跌家数柱状图（堆叠）----
  useEffect(() => {
    if (points.length === 0 || !adChartRef.current) return;
    if (!adChart.current) adChart.current = echarts.init(adChartRef.current);
    const chart = adChart.current;
    const dates = points.map((p) => p.trade_date);
    const ups   = points.map((p) => p.up_count);
    const downs = points.map((p) => -p.down_count);  // 取负值使向下绘制
    const flats = points.map((p) => p.flat_count);

    chart.setOption({
      ...ECHARTS_BASE_OPTION,
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1f1f1f",
        borderColor: "#333",
        textStyle: { color: "#e0e0e0", fontSize: 12 },
        formatter: (params: echarts.TooltipComponentFormatterCallbackParams) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const dateStr = String((params[0] as any).axisValue ?? params[0].name ?? "");
          const lines = params.map((p) => {
            const raw = Math.abs(Number(p.value));
            return `<span style="display:inline-block;margin-right:4px;border-radius:10px;width:8px;height:8px;background:${p.color}"></span>${p.seriesName}: ${raw}`;
          });
          return `${dateStr}<br/>${lines.join("<br/>")}`;
        },
      },
      legend: { data: ["上涨", "下跌", "平盘"], top: 4, textStyle: { color: "#d9d9d9", fontSize: 11 } },
      grid: { left: 50, right: 20, top: 36, bottom: 40 },
      xAxis: { type: "category", data: dates, axisLabel: { color: "#8c8c8c", fontSize: 10 }, axisLine: { lineStyle: { color: "#333" } } },
      yAxis: {
        type: "value",
        axisLabel: { color: "#8c8c8c", formatter: (v: number) => Math.abs(v).toString() },
        splitLine: { lineStyle: { color: "#222" } },
      },
      series: [
        {
          name: "上涨", type: "bar", stack: "ad", data: ups,
          itemStyle: { color: RISE_COLOR },
          barMaxWidth: 12,
        },
        {
          name: "平盘", type: "bar", stack: "ad", data: flats,
          itemStyle: { color: "#595959" },
          barMaxWidth: 12,
        },
        {
          name: "下跌", type: "bar", stack: "ad", data: downs,
          itemStyle: { color: "#1677ff" },
          barMaxWidth: 12,
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [points]);

  // 最新一日情绪摘要
  const latest = points[points.length - 1];
  const latestLabel = latest ? sentimentLabel(latest.sentiment_score) : null;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 1400, margin: "0 auto" }}>
      {/* 页头 */}
      <div>
        <Title level={4} style={{ margin: 0 }}>
          <FireOutlined style={{ marginRight: 6, color: "#fa8c16" }} />
          大V情绪仪表盘
        </Title>
        <Text type="secondary" style={{ fontSize: 13 }}>
          量化市场情绪走势，辅助判断涨停热度、多空力量对比与整体冷热节奏。
        </Text>
      </div>

      {/* 时间范围选择 */}
      <Row align="middle" gutter={12}>
        <Col>
          <Text type="secondary">观察区间：</Text>
        </Col>
        <Col>
          <Radio.Group
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            buttonStyle="solid"
            size="small"
          >
            <Radio.Button value={20}>近1月</Radio.Button>
            <Radio.Button value={60}>近3月</Radio.Button>
            <Radio.Button value={90}>近半年</Radio.Button>
            <Radio.Button value={120}>近半年+</Radio.Button>
          </Radio.Group>
        </Col>
      </Row>

      {loading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : (
        <>
          {/* 今日情绪摘要卡片 */}
          {latest && (
            <Row gutter={[16, 16]}>
              <Col xs={12} sm={6}>
                <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
                  <Statistic
                    title={
                      <Space size={4}>
                        <span>情绪分</span>
                        <Tooltip title="综合涨跌家数与涨停热度的合成指数（0~100）">
                          <InfoCircleOutlined style={{ fontSize: 11, color: "#8c8c8c" }} />
                        </Tooltip>
                      </Space>
                    }
                    value={latest.sentiment_score.toFixed(1)}
                    valueStyle={{ color: latestLabel!.color, fontSize: 26, fontWeight: 700 }}
                    suffix={<Tag color={latestLabel!.color} style={{ marginLeft: 4, fontSize: 11 }}>{latestLabel!.text}</Tag>}
                  />
                </Card>
              </Col>
              <Col xs={12} sm={6}>
                <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
                  <Statistic
                    title="涨停家数"
                    value={latest.limit_up_count}
                    suffix="只"
                    valueStyle={{ color: RISE_COLOR, fontSize: 26, fontWeight: 700 }}
                  />
                  <Text type="secondary" style={{ fontSize: 11 }}>占上涨 {latest.limit_up_ratio.toFixed(1)}%</Text>
                </Card>
              </Col>
              <Col xs={12} sm={6}>
                <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
                  <Statistic
                    title="上涨家数"
                    value={latest.up_count}
                    suffix="只"
                    valueStyle={{ color: RISE_COLOR, fontSize: 26, fontWeight: 700 }}
                  />
                  <Text type="secondary" style={{ fontSize: 11 }}>占全市场 {latest.up_ratio.toFixed(1)}%</Text>
                </Card>
              </Col>
              <Col xs={12} sm={6}>
                <Card size="small" styles={{ body: { padding: "14px 16px" } }}>
                  <Statistic
                    title="下跌家数"
                    value={latest.down_count}
                    suffix="只"
                    valueStyle={{ color: "#1677ff", fontSize: 26, fontWeight: 700 }}
                  />
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    跌停 {latest.limit_down_count} 只
                  </Text>
                </Card>
              </Col>
            </Row>
          )}

          {/* 情绪分趋势 */}
          <Card
            title={
              <Space>
                <FundOutlined />
                <span>情绪分趋势</span>
                <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                  ≥70 偏乐观，≤30 偏悲观；虚线为参考阈值
                </Text>
              </Space>
            }
          >
            <div ref={scoreChartRef} style={{ width: "100%", height: isMobile ? 180 : 240 }} />
          </Card>

          <Row gutter={16}>
            {/* 涨跌停数量趋势 */}
            <Col xs={24} md={12}>
              <Card
                title={
                  <Space>
                    <span>涨停 / 跌停数量趋势</span>
                    <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                      涨停激增通常预示情绪高峰
                    </Text>
                  </Space>
                }
              >
                <div ref={limitChartRef} style={{ width: "100%", height: isMobile ? 180 : 260 }} />
              </Card>
            </Col>
            {/* 涨跌家数分布 */}
            <Col xs={24} md={12}>
              <Card
                title={
                  <Space>
                    <span>涨跌家数分布</span>
                    <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                      红柱上涨 / 蓝柱下跌（向下）
                    </Text>
                  </Space>
                }
              >
                <div ref={adChartRef} style={{ width: "100%", height: isMobile ? 180 : 260 }} />
              </Card>
            </Col>
          </Row>

          {/* 大V解读说明 */}
          <Card size="small" style={{ borderStyle: "dashed" }}>
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                <b>大V视角解读</b>：情绪分连续 3 日 ≥ 70 → 市场可能出现追涨信号，须警惕连板退潮风险；
                连续 3 日 ≤ 30 → 极端恐慌区域，通常为超跌修复窗口。
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                涨停数量单日 &gt; 100 只（A 股 5000 只时约占 2%）通常视为热度较高的市场环境；
                跌停数大幅增加则需关注系统性风险。以上规则供参考，不构成投资建议。
              </Text>
            </Space>
          </Card>
        </>
      )}
    </Space>
  );
}
