/**
 * 股票复盘（V2.0.1）：单日市场情绪、三大股指、涨跌幅分布等。
 * 期间复盘占位禁用（与飞书范围一致）。
 */
import { Card, Col, DatePicker, Row, Space, Statistic, Tabs, Typography, message, theme } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import * as echarts from "echarts";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { type ReplayDailyOut, fetchReplayDaily, getApiErrorMessage } from "../api/client";

const { Text } = Typography;

function formatAmount(v: number): string {
  if (!v || v <= 0) return "—";
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
}

/** 根据指数当日涨跌幅选卡片底色：涨红、跌绿、平/无涨跌信息偏黑灰（A 股配色习惯）。 */
function indexCardBackground(
  dataOk: boolean,
  pct: number | null | undefined,
  neutralFill: string,
): string {
  if (!dataOk) return neutralFill;
  if (pct == null || Math.abs(pct) < 1e-9) return "rgba(15, 23, 42, 0.14)"; // slate-900 浅黑
  if (pct > 0) return "rgba(239, 68, 68, 0.12)";
  return "rgba(34, 197, 94, 0.12)";
}

export default function ReplayPage() {
  const { token } = theme.useToken();
  const navigate = useNavigate();
  const bucketRef = useRef<HTMLDivElement>(null);
  const bucketChart = useRef<echarts.ECharts | null>(null);

  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ReplayDailyOut | null>(null);
  const [picked, setPicked] = useState<Dayjs | null>(null);

  const load = useCallback(async (d: string | undefined) => {
    setLoading(true);
    try {
      const out = await fetchReplayDaily(
        d ? { trade_date: d, list_limit: 400 } : { list_limit: 400 },
      );
      setData(out);
      setPicked((prev) => prev ?? (out.trade_date ? dayjs(out.trade_date) : null));
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(undefined);
  }, []);

  useEffect(() => {
    if (!data?.buckets?.length || !bucketRef.current) return;
    if (!bucketChart.current) bucketChart.current = echarts.init(bucketRef.current);
    const labels = data.buckets.map((b) => b.label);
    const vals = data.buckets.map((b) => b.count);
    bucketChart.current.setOption({
      grid: { left: 48, right: 16, top: 36, bottom: 72 },
      xAxis: { type: "category", data: labels, axisLabel: { rotate: 35, fontSize: 10 } },
      yAxis: { type: "value", name: "家数" },
      series: [
        {
          type: "bar",
          data: vals,
          itemStyle: { color: "#2563eb", borderRadius: [4, 4, 0, 0] },
          // 柱顶展示该桶内的股票家数，便于直接读数
          label: { show: true, position: "top", formatter: "{c}" },
        },
      ],
    });
    const ro = new ResizeObserver(() => bucketChart.current?.resize());
    ro.observe(bucketRef.current);
    return () => ro.disconnect();
  }, [data?.buckets]);

  const onDateChange = (v: Dayjs | null) => {
    setPicked(v);
    if (v) void load(v.format("YYYY-MM-DD"));
  };

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        股票复盘
      </Typography.Title>
      <Tabs
        items={[
          { key: "day", label: "单日复盘" },
          {
            key: "range",
            label: "期间复盘",
            disabled: true,
          },
        ]}
      />
      <Card
        loading={loading}
        style={{ borderRadius: 12, borderColor: token.colorBorderSecondary, marginBottom: 16 }}
        styles={{ body: { padding: 24 } }}
      >
        <Space wrap style={{ marginBottom: 16 }}>
          <Text type="secondary">交易日</Text>
          <DatePicker value={picked} onChange={onDateChange} allowClear={false} />
          {data?.latest_bar_date && (
            <Text type="secondary">本地最新日线：{data.latest_bar_date}</Text>
          )}
        </Space>
        {data?.universe_note && (
          <Text type="secondary" style={{ display: "block", marginBottom: 16, fontSize: 12 }}>
            {data.universe_note}
          </Text>
        )}

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
                          <span style={{ fontSize: 14, marginLeft: 8 }}>
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
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="涨跌家数" style={{ borderRadius: 12 }}>
            <Space size="large" wrap>
              <Statistic title="上涨" value={data?.up_count ?? 0} valueStyle={{ color: "#ef4444" }} />
              <Statistic title="平盘" value={data?.flat_count ?? 0} />
              <Statistic title="下跌" value={data?.down_count ?? 0} valueStyle={{ color: "#22c55e" }} />
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="涨跌停家数" style={{ borderRadius: 12 }}>
            <Space size="large" wrap>
              <Statistic title="涨停" value={data?.limit_up_count ?? 0} valueStyle={{ color: "#ef4444" }} />
              <Statistic title="跌停" value={data?.limit_down_count ?? 0} valueStyle={{ color: "#22c55e" }} />
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card title="涨跌幅分布" style={{ borderRadius: 12 }}>
            <div ref={bucketRef} style={{ height: 320 }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
