/**
 * 个股列表页面
 *
 * 功能：按交易日展示全市场所有股票的当日行情数据，包括：
 * - 价格（收盘、开盘、高、低）
 * - 涨跌幅、换手率、成交量、成交额
 *
 * 支持功能：
 * - 日期切换（选择不同交易日查看历史数据）
 * - 多字段筛选（代码、名称、市场、数值区间等）
 * - 列头点击排序 + 分页
 * - 代码列可点击跳转到对应股票的 K 线页
 *
 * 注意：这里展示的是行情数据，不含同步状态、复权因子等运维信息。
 * 运维相关请使用「数据后台 · 数据池」页面。
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
import { FALL_COLOR, FLAT_COLOR, RISE_COLOR, zebraRowClass } from "../constants/theme";

const { Text, Paragraph } = Typography;

/**
 * 把成交额（元）格式化为"X 亿"或"X 万"，便于快速扫读
 * 例：1234567890 → "12.35 亿"
 */
function fmtAmount(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return v.toFixed(0);
}

/**
 * 把成交量（手，1 手 = 100 股）格式化为"X 万"或整数，便于快速扫读
 */
function fmtVol(v: number): string {
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)} 万`;
  return String(Math.round(v));
}

/**
 * 把任意类型的值转换为 number，转换失败则返回 undefined
 * 用于从表单中安全地读取数值型输入，避免空字符串或 null 引起接口错误
 */
function numOrUndef(raw: unknown): number | undefined {
  if (raw === null || raw === undefined || raw === "") return undefined;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

/**
 * 从表单值构造后端筛选参数
 * 规则：空字符串和未填写的数值字段不传给接口（避免无效查询键）
 *
 * @param values - 表单的 getFieldsValue() 返回值
 * @returns 去掉空值后的筛选参数对象
 */
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
  // token：读取当前主题颜色变量（边框色、背景色等）
  const { token } = theme.useToken();
  // screens：响应式断点检测，用于在窄屏下隐藏开/高/低列
  const screens = Grid.useBreakpoint();
  // 只有大屏（lg 及以上）才显示开、高、低三列，窄屏隐藏减少横向滚动
  const showOhlcExtras = Boolean(screens.lg);

  // loading：是否正在请求数据（控制表格加载状态）
  const [loading, setLoading] = useState(false);
  // picked：日期选择器当前选中的日期
  const [picked, setPicked] = useState<Dayjs | null>(null);
  // page / pageSize：分页状态
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  // sortField / sortOrder：当前排序字段和方向（asc 升序 / desc 降序）
  const [sortField, setSortField] = useState<DailyUniverseSort>("pct_change");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  // total：当前筛选条件下的总记录数（用于分页显示"共 X 条"）
  const [total, setTotal] = useState(0);
  // items：当前页的数据行
  const [items, setItems] = useState<DailyUniverseRow[]>([]);
  // tradeDateStr：后端实际返回的交易日（可能与 picked 不同，如周末自动取最近交易日）
  const [tradeDateStr, setTradeDateStr] = useState<string | null>(null);
  // latestBar：本地数据库中最新的一条日线日期（用于提示数据是否滞后）
  const [latestBar, setLatestBar] = useState<string | null>(null);
  // appliedFilters：已提交的筛选条件（点"应用筛选"后才更新，避免输入过程中频繁请求）
  const [appliedFilters, setAppliedFilters] = useState<DailyUniverseFilterParams>({});
  // filterForm：筛选表单实例，用于 getFieldsValue / resetFields
  const [filterForm] = Form.useForm();

  /**
   * 向后端请求当前分页 + 筛选 + 排序条件下的个股列表数据
   * 依赖项变化时会自动重新触发（通过 useEffect）
   */
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
      // 首次加载时，把日期选择器定位到后端返回的实际交易日
      if (!picked && out.trade_date) setPicked(dayjs(out.trade_date));
    } catch (e) {
      message.error(getApiErrorMessage(e));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [picked, page, pageSize, sortField, sortOrder, appliedFilters]);

  // load 依赖变化时自动重新请求（换页、改排序、改筛选条件等都会触发）
  useEffect(() => {
    void load();
  }, [load]);

  /**
   * 给列头返回当前的排序方向（用于 Ant Design 列头显示排序箭头）
   * @param field - 字段名
   * @returns "ascend" | "descend" | undefined（未排序时为 undefined）
   */
  const sortOrderForCol = (field: DailyUniverseSort): SortOrder | undefined =>
    sortField === field ? (sortOrder === "asc" ? "ascend" : "descend") : undefined;

  /**
   * 表格列定义
   * useMemo 避免每次渲染都重新构造列数组（性能优化）
   */
  const columns: ColumnsType<DailyUniverseRow> = useMemo(() => {
    // 涨跌幅列：正值红色、负值绿色、零值灰色（A 股配色）
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
          <Text
            strong
            style={{
              color: v > 0 ? RISE_COLOR : v < 0 ? FALL_COLOR : FLAT_COLOR,
            }}
          >
            {v > 0 ? "+" : ""}
            {v.toFixed(2)}
          </Text>
        ),
    };

    // 开/高/低列：只在大屏（lg+）时显示，小屏隐藏节省空间
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
        // 股票代码列：可点击跳转到对应 K 线页
        title: "代码",
        dataIndex: "ts_code",
        width: 108,
        fixed: "left" as const, // 左固定，横向滚动时始终可见
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

  /**
   * 表格分页/排序变化时的回调
   *
   * 注意：Ant Design 的 onChange 在分页、筛选、排序三种操作时都会触发，
   * 需要通过 extra.action 判断是哪种操作，避免误处理。
   * 第三次点击同一列头时 order 为 null（Ant Design"取消排序"状态），
   * 此时后端仍需明确 asc/desc，故对当前列做一次方向反转。
   */
  const onTableChange = (
    pag: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<DailyUniverseRow> | SorterResult<DailyUniverseRow>[],
    extra: TableCurrentDataSource<DailyUniverseRow>,
  ) => {
    if (pag.current != null) setPage(pag.current);
    if (pag.pageSize != null) setPageSize(pag.pageSize);

    // 只在用户点击列头排序时处理 sorter，其他操作（换页等）不处理
    if (extra.action !== "sort") return;

    const one = Array.isArray(sorter) ? sorter[0] : sorter;
    if (!one) return;

    const rawField = one.field != null ? one.field : sortField;
    const field = String(Array.isArray(rawField) ? rawField[0] : rawField) as DailyUniverseSort;
    // 白名单校验：只接受已知的可排序字段
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
      // order 为 null：取消排序状态 → 反转方向
      setSortField(field);
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
    }
    setPage(1); // 排序变化后回到第一页
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
        {/* ── 页面标题 + 功能说明 ──────────────────────────── */}
        <div>
          <Typography.Title level={4} style={{ marginBottom: 8 }}>
            个股列表
          </Typography.Title>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 720 }}>
            查看某一交易日全市场个股的行情表现（与是否已同步、K
            线条数无关；运维与补数请使用「数据后台 · 数据池」）。支持按代码/名称/市场等关键词与数值区间筛选，筛选在后端对当日全市场结果集生效后再排序分页。
          </Paragraph>
        </div>

        {/* ── 筛选 + 数据表格卡片 ───────────────────────────── */}
        <Card
          style={{
            borderRadius: 16,
            borderColor: token.colorBorderSecondary,
            boxShadow: "0 1px 2px 0 rgb(15 23 42 / 0.04)",
          }}
          styles={{
            header: {
              borderBottom: `1px solid ${token.colorBorderSecondary}`,
              background: token.colorBgElevated, // 暗色主题下的略亮背景
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
                    setPage(1); // 切换日期后回到第一页
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
          {/* 折叠筛选面板：默认展开，用户可折叠节省屏幕空间 */}
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
                label: "筛选条件（数值区间可只填一侧）",
                children: (
                  <Form form={filterForm} layout="vertical" size="small">
                    {/* 第一行：文字类筛选 */}
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
                    {/* 第二行：价格类数值区间 */}
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
                    {/* 第三行：更多价格 + 成交量 */}
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
                    {/* 第四行：成交额、换手率、操作按钮 */}
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
                            {/* 应用筛选：读取表单当前值，写入 appliedFilters 触发 load */}
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
                            {/* 重置：清空表单并清除已应用的筛选条件 */}
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

          {/* 数据表格：斑马纹 + 固定表头 + 分页 */}
          <div style={{ width: "100%", overflow: "auto" }}>
            <Table<DailyUniverseRow>
              size="small"
              rowKey="ts_code"
              loading={loading}
              columns={columns}
              dataSource={items}
              rowClassName={zebraRowClass} // 斑马纹：偶数行略微加亮
              sticky={{ offsetHeader: 0 }} // 固定表头：滚动时表头始终可见
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
