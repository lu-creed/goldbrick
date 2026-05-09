/**
 * StockFundamentalsPanel — K 线页下方的个股财务快照面板
 *
 * 展示内容：
 *  - 公司基本信息（名称/市场/上市日期）
 *  - PE(TTM) / PB / 总市值 / 流通市值 / 最新价 / 换手率
 *  - PE 和 PB 近 60 日历史趋势小图（lightweight-charts）
 *  - 大V看板信息（派息率 / EPS / 预期股息率 / 分类），仅登录且在看板中的股票展示
 *  - 近 5 年年度财务指标表（ROE/毛利率/负债率/营收/净利润），实时从 AKShare 拉取
 */
import {
  ColorType,
  LineSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { Card, Col, Row, Skeleton, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { FinancialIndicatorRow, FundamentalSnapshot, PEPBPoint } from "../api/client";
import { fetchFinancialIndicators } from "../api/client";

const { Text } = Typography;

// ── 营收/净利润格式化 ─────────────────────────────────────────────────────────

function formatMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(2)} 万亿`;
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)} 亿`;
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)} 万`;
  return `${sign}${abs.toFixed(0)}`;
}

function fmtPct(v: number | null | undefined): string {
  return v != null ? `${v.toFixed(2)}%` : "—";
}

function formatMv(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)} 万亿`;
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  return `${(v / 1e4).toFixed(0)} 万`;
}

// ── 小折线图组件 ───────────────────────────────────────────────────────────────

interface MiniChartProps {
  points: PEPBPoint[];
  color: string;
  height?: number;
}

function MiniLineChart({ points, color, height = 100 }: MiniChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current || points.length === 0) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const container = containerRef.current;
    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8c8c8c",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#2a2a2a" },
        horzLines: { color: "#2a2a2a" },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
      leftPriceScale: { visible: false },
      timeScale: { borderVisible: false, rightOffset: 2 },
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      handleScale: false,
      handleScroll: false,
      width: container.clientWidth,
      height,
    });
    chartRef.current = chart;

    const ser = chart.addSeries(LineSeries, {
      color,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    const data = points
      .filter((p) => p.value != null)
      .map((p) => ({ time: p.date as unknown as UTCTimestamp, value: p.value as number }));
    ser.setData(data);
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [points, color, height]);

  return <div ref={containerRef} style={{ width: "100%", height }} />;
}

// ── 主组件 ─────────────────────────────────────────────────────────────────────

interface Props {
  snapshot: FundamentalSnapshot | null;
  loading?: boolean;
}

const DAV_CLASS_COLOR: Record<string, string> = {
  A: "gold",
  B: "blue",
  C: "green",
  D: "volcano",
};

const fiColumns: ColumnsType<FinancialIndicatorRow> = [
  { title: "年份", dataIndex: "period", width: 64 },
  { title: "ROE", dataIndex: "roe", width: 80, render: fmtPct },
  { title: "毛利率", dataIndex: "gross_margin", width: 80, render: fmtPct },
  { title: "资产负债率", dataIndex: "debt_ratio", width: 90, render: fmtPct },
  { title: "营业收入", dataIndex: "revenue", render: formatMoney },
  { title: "净利润", dataIndex: "net_profit", render: formatMoney },
];

export default function StockFundamentalsPanel({ snapshot, loading }: Props) {
  const [fiRows, setFiRows] = useState<FinancialIndicatorRow[]>([]);
  const [fiLoading, setFiLoading] = useState(false);

  useEffect(() => {
    if (!snapshot?.ts_code) { setFiRows([]); return; }
    setFiLoading(true);
    fetchFinancialIndicators(snapshot.ts_code)
      .then(setFiRows)
      .catch(() => setFiRows([]))
      .finally(() => setFiLoading(false));
  }, [snapshot?.ts_code]);
  if (loading) {
    return (
      <Card size="small" style={{ marginTop: 12 }}>
        <Skeleton active paragraph={{ rows: 3 }} />
      </Card>
    );
  }

  if (!snapshot) return null;

  const exchangeLabel =
    snapshot.exchange === "SSE" ? "上交所" :
    snapshot.exchange === "SZSE" ? "深交所" :
    snapshot.exchange === "BSE" ? "北交所" :
    (snapshot.exchange ?? "");

  const hasDav = snapshot.dav_payout_ratio != null || snapshot.dav_eps != null || snapshot.dav_class != null;
  const hasHistory = snapshot.pe_history.length > 1 || snapshot.pb_history.length > 1;

  return (
    <Card
      size="small"
      style={{ marginTop: 12 }}
      title={
        <span style={{ fontSize: 13, color: "#d9d9d9" }}>
          {snapshot.name && <strong style={{ marginRight: 8 }}>{snapshot.name}</strong>}
          <Text type="secondary" style={{ fontSize: 12 }}>{snapshot.ts_code}</Text>
          {snapshot.market && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {snapshot.market}
              {exchangeLabel && ` · ${exchangeLabel}`}
            </Text>
          )}
          {snapshot.list_date && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 12 }}>
              上市 {snapshot.list_date}
            </Text>
          )}
        </span>
      }
    >
      {/* 估值 + 行情指标 */}
      <Row gutter={[16, 8]} style={{ marginBottom: hasHistory ? 12 : 0 }}>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>PE(TTM)</span>}
            value={snapshot.pe_ttm != null ? snapshot.pe_ttm.toFixed(1) : "—"}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>PB</span>}
            value={snapshot.pb != null ? snapshot.pb.toFixed(2) : "—"}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>总市值</span>}
            value={formatMv(snapshot.total_mv)}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>流通市值</span>}
            value={formatMv(snapshot.circ_mv)}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>最新价</span>}
            value={snapshot.latest_close != null ? snapshot.latest_close.toFixed(2) : "—"}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
        <Col xs={8} sm={4}>
          <Statistic
            title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>换手率</span>}
            value={snapshot.latest_turnover_rate != null ? `${snapshot.latest_turnover_rate.toFixed(2)}%` : "—"}
            valueStyle={{ fontSize: 15 }}
          />
        </Col>
      </Row>

      {/* PE/PB 历史趋势小图 */}
      {hasHistory && (
        <Row gutter={[16, 0]} style={{ marginBottom: hasDav ? 12 : 0 }}>
          <Col xs={24} sm={12}>
            <Text style={{ fontSize: 11, color: "#8c8c8c", display: "block", marginBottom: 4 }}>
              PE(TTM) 近期走势
              {snapshot.fundamental_date && (
                <span style={{ marginLeft: 6 }}>（数据截至 {snapshot.fundamental_date}）</span>
              )}
            </Text>
            {snapshot.pe_history.some((p) => p.value != null) ? (
              <MiniLineChart points={snapshot.pe_history} color="#ffd666" height={100} />
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>暂无 PE 数据，请先同步基本面</Text>
            )}
          </Col>
          <Col xs={24} sm={12}>
            <Text style={{ fontSize: 11, color: "#8c8c8c", display: "block", marginBottom: 4 }}>
              PB 近期走势
            </Text>
            {snapshot.pb_history.some((p) => p.value != null) ? (
              <MiniLineChart points={snapshot.pb_history} color="#69c0ff" height={100} />
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>暂无 PB 数据，请先同步基本面</Text>
            )}
          </Col>
        </Row>
      )}

      {/* 大V看板信息 */}
      {hasDav && (
        <Row gutter={[16, 4]} style={{ borderTop: "1px solid #2a2a2a", paddingTop: 8 }}>
          {snapshot.dav_class && (
            <Col>
              <Text style={{ fontSize: 11, color: "#8c8c8c" }}>大V分类</Text>
              <br />
              <Tag color={DAV_CLASS_COLOR[snapshot.dav_class] ?? "default"} style={{ marginTop: 4 }}>
                {snapshot.dav_class} 类
              </Tag>
            </Col>
          )}
          {snapshot.dav_payout_ratio != null && (
            <Col>
              <Statistic
                title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>派息率</span>}
                value={`${snapshot.dav_payout_ratio.toFixed(1)}%`}
                valueStyle={{ fontSize: 14 }}
              />
            </Col>
          )}
          {snapshot.dav_eps != null && (
            <Col>
              <Statistic
                title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>EPS (元)</span>}
                value={snapshot.dav_eps.toFixed(2)}
                valueStyle={{ fontSize: 14 }}
              />
            </Col>
          )}
          {snapshot.expected_yield != null && (
            <Col>
              <Statistic
                title={<span style={{ fontSize: 11, color: "#8c8c8c" }}>预期股息率</span>}
                value={`${snapshot.expected_yield.toFixed(2)}%`}
                valueStyle={{ fontSize: 14, color: "#f5222d" }}
              />
            </Col>
          )}
        </Row>
      )}

      {/* 年度财务指标表 */}
      {(fiLoading || fiRows.length > 0) && (
        <div style={{ borderTop: "1px solid #2a2a2a", paddingTop: 8, marginTop: 8 }}>
          <Text style={{ fontSize: 11, color: "#8c8c8c", display: "block", marginBottom: 6 }}>
            年度财务指标（来自 AKShare，实时拉取）
          </Text>
          <Table<FinancialIndicatorRow>
            size="small"
            loading={fiLoading}
            dataSource={fiRows}
            rowKey="period"
            pagination={false}
            columns={fiColumns}
          />
        </div>
      )}
    </Card>
  );
}
