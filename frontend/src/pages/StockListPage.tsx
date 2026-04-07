/**
 * 数据看板 · 个股列表：按交易日展示全市场个股的行情字段（价量、涨跌幅、换手等）。
 * 与「数据后台 · 数据池」解耦——不展示同步条数、复权因子是否齐全等运维向信息。
 */
import {
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Flex,
  Form,
  Grid,
  Input,
  InputNumber,
  Row,
  Table,
  Typography,
  message,
  theme,
} from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import type {
  FilterValue,
  SorterResult,
  SortOrder,
  TableCurrentDataSource,
} from "antd/es/table/interface";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  type DailyUniverseFilterParams,
  type DailyUniverseRow,
  type DailyUniverseSort,
  fetchDailyUniverse,
  getApiErrorMessage,
} from "../api/client";

const { Text, Paragraph } = Typography;

function fmtAmount(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
}

function fmtVol(v: number): string {
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return String(Math.round(v));
}

function numOrUndef(raw: unknown): number | undefined {
  if (raw === null || raw === undefined || raw === "") return undefined;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

/** 从表单取值构造接口筛选参数：去掉空字符串与空数字，避免无效查询键 */
function filterParamsFromForm(values: Record<string, unknown>): DailyUniverseFilterParams {
  const out: DailyUniverseFilterParams = {};
  const s = (raw: unknown) => (typeof raw === "string" ? raw.trim() : "");
  if (s(values.code_contains)) out.code_contains = s(values.code_contains);
  if (s(values.name_contains)) out.name_contains = s(values.name_contains);
  if (s(values.market_contains)) out.market_contains = s(values.market_contains);
  if (s(values.exchange_contains)) out.exchange_contains = s(values.exchange_contains);
  const pm = numOrUndef(values.pct_min);
  const px = numOrUndef(values.pct_max);
  const om = numOrUndef(values.open_min);
  const ox = numOrUndef(values.open_max);
  const hm = numOrUndef(values.high_min);
  const hx = numOrUndef(values.high_max);
  const lm = numOrUndef(values.low_min);
  const lx = numOrUndef(values.low_max);
  const cm = numOrUndef(values.close_min);
  const cx = numOrUndef(values.close_max);
  const vm = numOrUndef(values.volume_min);
  const vx = numOrUndef(values.volume_max);
  const am = numOrUndef(values.amount_min);
  const ax = numOrUndef(values.amount_max);
  const tm = numOrUndef(values.turnover_min);
  const tx = numOrUndef(values.turnover_max);
  if (pm !== undefined) out.pct_min = pm;
  if (px !== undefined) out.pct_max = px;
  if (om !== undefined) out.open_min = om;
  if (ox !== undefined) out.open_max = ox;
  if (hm !== undefined) out.high_min = hm;
  if (hx !== undefined) out.high_max = hx;
  if (lm !== undefined) out.low_min = lm;
  if (lx !== undefined) out.low_max = lx;
  if (cm !== undefined) out.close_min = cm;
  if (cx !== undefined) out.close_max = cx;
  if (vm !== undefined) out.volume_min = vm;
  if (vx !== undefined) out.volume_max = vx;
  if (am !== undefined) out.amount_min = am;
  if (ax !== undefined) out.amount_max = ax;
  if (tm !== undefined) out.turnover_min = tm;
  if (tx !== undefined) out.turnover_max = tx;
  return out;
}

export default function StockListPage() {
  const { token } = theme.useToken();
  /** 断点：窄屏隐藏开/高/低，减少横向滚动与拥挤感 */
  const screens = Grid.useBreakpoint();
  const showOhlcExtras = Boolean(screens.lg);
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<Dayjs | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortField, setSortField] = useState<DailyUniverseSort>("pct_change");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<DailyUniverseRow[]>([]);
  const [tradeDateStr, setTradeDateStr] = useState<string | null>(null);
  const [latestBar, setLatestBar] = useState<string | null>(null);
  /** 仅「应用筛选」后写入，避免输入过程中频繁请求 */
  const [appliedFilters, setAppliedFilters] = useState<DailyUniverseFilterParams>({});
  const [filterForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = picked ? picked.format("YYYY-MM-DD") : undefined;
      const out = await fetchDailyUniverse({
        trade_date: d,
        page,
        page_size: pageSize,
        sort: sortField,
        order: sortOrder,
        ...appliedFilters,
      });
      setItems(out.items);
      setTotal(out.total);
      setTradeDateStr(out.trade_date);
      setLatestBar(out.latest_bar_date);
      if (!picked && out.trade_date) setPicked(dayjs(out.trade_date));
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [picked, page, pageSize, sortField, sortOrder, appliedFilters]);

  useEffect(() => {
    void load();
  }, [load]);

  const sortOrderForCol = (field: DailyUniverseSort): SortOrder | undefined =>
    sortField === field ? (sortOrder === "asc" ? "ascend" : "descend") : undefined;

  const columns: ColumnsType<DailyUniverseRow> = useMemo(() => {
    const pctCol = {
      title: "涨跌幅%",
      dataIndex: "pct_change",
      width: 96,
      align: "right" as const,
      sorter: true,
      sortOrder: sortOrderForCol("pct_change"),
      render: (v: number | null) =>
        v == null ? (
          "—"
        ) : (
          <Text type={v > 0 ? "danger" : v < 0 ? "success" : "secondary"} strong>
            {v.toFixed(2)}
          </Text>
        ),
    };
    const ohlc: ColumnsType<DailyUniverseRow> = showOhlcExtras
      ? [
          {
            title: "开盘",
            dataIndex: "open",
            width: 88,
            align: "right" as const,
            render: (v: number) => v.toFixed(2),
          },
          {
            title: "高",
            dataIndex: "high",
            width: 72,
            align: "right" as const,
            render: (v: number) => v.toFixed(2),
          },
          {
            title: "低",
            dataIndex: "low",
            width: 72,
            align: "right" as const,
            render: (v: number) => v.toFixed(2),
          },
        ]
      : [];
    return [
      {
        title: "代码",
        dataIndex: "ts_code",
        width: 108,
        fixed: "left" as const,
        sorter: true,
        sortOrder: sortOrderForCol("ts_code"),
        render: (c: string) => (
          <Link to={`/?ts_code=${encodeURIComponent(c)}`} style={{ color: token.colorPrimary }}>
            {c}
          </Link>
        ),
      },
      { title: "名称", dataIndex: "name", width: 108, ellipsis: true },
      { title: "市场", dataIndex: "market", width: 72, ellipsis: true },
      pctCol,
      {
        title: "收盘",
        dataIndex: "close",
        width: 88,
        align: "right" as const,
        sorter: true,
        sortOrder: sortOrderForCol("close"),
        render: (v: number) => v.toFixed(2),
      },
      ...ohlc,
      {
        title: "换手%",
        dataIndex: "turnover_rate",
        width: 88,
        align: "right" as const,
        sorter: true,
        sortOrder: sortOrderForCol("turnover_rate"),
        render: (v: number | null) => (v == null ? "—" : v.toFixed(2)),
      },
      {
        title: "成交量",
        dataIndex: "volume",
        width: 100,
        align: "right" as const,
        sorter: true,
        sortOrder: sortOrderForCol("volume"),
        render: (v: number) => fmtVol(v),
      },
      {
        title: "成交额",
        dataIndex: "amount",
        width: 100,
        align: "right" as const,
        sorter: true,
        sortOrder: sortOrderForCol("amount"),
        render: (v: number) => fmtAmount(v),
      },
    ];
  }, [sortField, sortOrder, token.colorPrimary, showOhlcExtras]);

  const onTableChange = (
    pag: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<DailyUniverseRow> | SorterResult<DailyUniverseRow>[],
    extra: TableCurrentDataSource<DailyUniverseRow>,
  ) => {
    if (pag.current != null) setPage(pag.current);
    if (pag.pageSize != null) setPageSize(pag.pageSize);

    // 仅在用户点击列头排序时处理 sorter：分页/筛选触发的 onChange 也会带上当前 sorter 快照，
    // 若不加判断会误把页码锁回第 1 页；第三次点击列头时 order 为 null（Ant Design「取消排序」），
    // 后端查询仍需明确 asc/desc，故对当前列做一次反向切换。
    if (extra.action !== "sort") return;

    const one = Array.isArray(sorter) ? sorter[0] : sorter;
    if (!one) return;

    const rawField = one.field != null ? one.field : sortField;
    const field = String(Array.isArray(rawField) ? rawField[0] : rawField) as DailyUniverseSort;
    const allowed: DailyUniverseSort[] = [
      "ts_code",
      "pct_change",
      "close",
      "volume",
      "amount",
      "turnover_rate",
    ];
    if (!allowed.includes(field)) return;

    if (one.order === "ascend") {
      setSortField(field);
      setSortOrder("asc");
    } else if (one.order === "descend") {
      setSortField(field);
      setSortOrder("desc");
    } else {
      setSortField(field);
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
    }
    setPage(1);
  };

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 1200,
        marginInline: "auto",
        boxSizing: "border-box",
        paddingInline: 0,
      }}
    >
      <Flex vertical gap={20}>
        <div>
          <Typography.Title level={4} style={{ marginBottom: 8 }}>
            个股列表
          </Typography.Title>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 720 }}>
            查看某一交易日全市场个股的行情表现（与是否已同步、K
            线条数无关；运维与补数请使用「数据后台 · 数据池」）。支持按代码/名称/市场等关键词与数值区间筛选，筛选在后端对当日全市场结果集生效后再排序分页。
          </Paragraph>
        </div>

        <Card
          style={{
            borderRadius: 16,
            borderColor: token.colorBorderSecondary,
            boxShadow: "0 1px 2px 0 rgb(15 23 42 / 0.04)",
          }}
          styles={{
            header: {
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
              background: "#f8fafc",
            },
            body: { padding: "16px 16px 12px" },
          }}
          title={
            <Flex vertical gap={8} style={{ width: "100%" }}>
              <Flex wrap="wrap" align="center" gap={12}>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  交易日
                </Text>
                <DatePicker
                  value={picked}
                  onChange={(d) => {
                    setPicked(d);
                    setPage(1);
                  }}
                  allowClear={false}
                  style={{ minWidth: 140 }}
                />
              </Flex>
              {tradeDateStr ? (
                <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.5 }}>
                  数据日 {tradeDateStr}
                  {latestBar && latestBar !== tradeDateStr ? ` · 本地最新 bar ${latestBar}` : ""}
                  ，共 {total} 只个股
                </Text>
              ) : null}
            </Flex>
          }
        >
          <Collapse
            bordered={false}
            defaultActiveKey={["filters"]}
            style={{
              marginBottom: 12,
              background: token.colorBgContainer,
            }}
            items={[
              {
                key: "filters",
                label: "筛选条件（应用后请求接口；数值区间可只填一侧）",
                children: (
                  <Form form={filterForm} layout="vertical" size="small">
                    <Row gutter={[16, 4]}>
                      <Col xs={24} sm={12} md={6}>
                        <Form.Item name="code_contains" label="代码包含">
                          <Input allowClear placeholder="如 600000 / SH" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={6}>
                        <Form.Item name="name_contains" label="名称包含">
                          <Input allowClear placeholder="股票简称" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={6}>
                        <Form.Item name="market_contains" label="市场包含">
                          <Input allowClear />
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={6}>
                        <Form.Item name="exchange_contains" label="交易所包含">
                          <Input allowClear />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={[16, 4]}>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="涨跌幅 %（闭区间）">
                          <Flex gap={8}>
                            <Form.Item name="pct_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="pct_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="收盘价">
                          <Flex gap={8}>
                            <Form.Item name="close_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="close_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="开盘价">
                          <Flex gap={8}>
                            <Form.Item name="open_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="open_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={[16, 4]}>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="最高价">
                          <Flex gap={8}>
                            <Form.Item name="high_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="high_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="最低价">
                          <Flex gap={8}>
                            <Form.Item name="low_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="low_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="成交量（股）">
                          <Flex gap={8}>
                            <Form.Item name="volume_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="volume_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={[16, 4]}>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="成交额（元）">
                          <Flex gap={8}>
                            <Form.Item name="amount_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="amount_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label="换手率 %">
                          <Flex gap={8}>
                            <Form.Item name="turnover_min" noStyle>
                              <InputNumber placeholder="最小" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                            <Form.Item name="turnover_max" noStyle>
                              <InputNumber placeholder="最大" style={{ width: "100%" }} controls={false} />
                            </Form.Item>
                          </Flex>
                        </Form.Item>
                      </Col>
                      <Col xs={24} sm={12} md={8}>
                        <Form.Item label=" " colon={false}>
                          <Flex wrap="wrap" gap={8}>
                            <Button
                              type="primary"
                              onClick={() => {
                                const payload = filterParamsFromForm(filterForm.getFieldsValue());
                                setAppliedFilters(payload);
                                setPage(1);
                              }}
                            >
                              应用筛选
                            </Button>
                            <Button
                              onClick={() => {
                                filterForm.resetFields();
                                setAppliedFilters({});
                                setPage(1);
                              }}
                            >
                              重置
                            </Button>
                          </Flex>
                        </Form.Item>
                      </Col>
                    </Row>
                  </Form>
                ),
              },
            ]}
          />
          <div style={{ width: "100%", overflow: "auto" }}>
            <Table<DailyUniverseRow>
              size="small"
              rowKey="ts_code"
              loading={loading}
              columns={columns}
              dataSource={items}
              sticky={{ offsetHeader: 0 }}
              style={{ minWidth: "100%" }}
              scroll={{ x: "max-content" }}
              pagination={{
                current: page,
                pageSize,
                total,
                showSizeChanger: true,
                pageSizeOptions: [20, 50, 100, 200],
                showTotal: (t) => `共 ${t} 条`,
                responsive: true,
                style: { marginBottom: 0 },
              }}
              onChange={onTableChange}
            />
          </div>
        </Card>
      </Flex>
    </div>
  );
}
