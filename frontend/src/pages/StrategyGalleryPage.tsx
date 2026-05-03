/**
 * 策略广场(Phase 2:产品易用性迭代)。
 *
 * 12 个开箱即用的策略卡片,用人话 + 预跑回测数据,让用户不用先学 DSL 就能下手。
 *
 * 交互:
 *   1. 顶部分类筛选(逆势/趋势/突破/价值/全部)
 *   2. 卡片点「用这个策略」→ /backtest?preset=<strategy_id> → BacktestPage 检测 query 拉策略填表
 *   3. 卡片点「查看完整介绍」→ 弹 Modal 展示 long_description + good_for/bad_for 详表
 *
 * 卡片上显示的预跑数据是**硬写的参考值**(见 strategy_seed.py),不是真实跑出的结果 —
 * 所以页面底部必须显示一条免责说明,并鼓励用户点进去自己跑真实数据。
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Modal,
  Row,
  Segmented,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { useNavigate } from "react-router-dom";
import {
  type StrategyGalleryCard,
  fetchStrategyGallery,
  getApiErrorMessage,
} from "../api/client";
import { FALL_COLOR, RISE_COLOR } from "../constants/theme";
import { useAuth } from "../hooks/useAuth";

const { Text, Paragraph, Title } = Typography;

// 分类 → 标签颜色(保持与图表主色调一致)
const CATEGORY_COLOR: Record<string, string> = {
  逆势: "blue",
  趋势: "purple",
  突破: "orange",
  价值: "green",
};

type CategoryFilter = "全部" | "逆势" | "趋势" | "突破" | "价值";


export default function StrategyGalleryPage() {
  const navigate = useNavigate();
  const { isGuest, openLoginGate } = useAuth();
  const [cards, setCards] = useState<StrategyGalleryCard[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<CategoryFilter>("全部");
  const [detailCard, setDetailCard] = useState<StrategyGalleryCard | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const data = await fetchStrategyGallery();
        if (!cancelled) setCards(data);
      } catch (e) {
        if (!cancelled) message.error(getApiErrorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    if (filter === "全部") return cards;
    return cards.filter((c) => c.category === filter);
  }, [cards, filter]);

  const handleUse = (card: StrategyGalleryCard) => {
    if (card.strategy_id == null) {
      message.warning("该预置策略的底层指标尚未就绪,请联系管理员检查后端种子数据");
      return;
    }
    const doNavigate = () => navigate(`/backtest?preset=${card.strategy_id}`);
    // 访客软挡:弹 Modal 提示登录,登录成功后自动跳转回测页预填参数
    if (isGuest) {
      openLoginGate({
        message: `登录后即可回测策略「${card.display_name}」,参数将自动预填,你只需确认时间范围和初始资金`,
        onSuccess: doNavigate,
      });
      return;
    }
    doNavigate();
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Title level={3} style={{ margin: 0 }}>策略广场</Title>
        <Paragraph type="secondary" style={{ margin: "4px 0 0 0" }}>
          12 个开箱即用的策略,不用懂 DSL 就能开始。点「用这个策略」→ 到回测页确认时间和资金 → 一键回测。
        </Paragraph>
      </div>

      <Segmented<CategoryFilter>
        value={filter}
        onChange={setFilter}
        options={["全部", "逆势", "趋势", "突破", "价值"]}
      />

      {loading ? (
        <Card loading style={{ minHeight: 240 }} />
      ) : filtered.length === 0 ? (
        <Empty description="该分类下暂无策略" />
      ) : (
        <Row gutter={[16, 16]}>
          {filtered.map((card) => (
            <Col xs={24} sm={12} lg={8} xxl={6} key={card.code}>
              <StrategyCard
                card={card}
                onUse={() => handleUse(card)}
                onDetail={() => setDetailCard(card)}
              />
            </Col>
          ))}
        </Row>
      )}

      {/* 免责说明:预跑数据是参考值,不是真实跑的结果 */}
      <Alert
        type="info"
        showIcon
        message="卡片上的回测数据仅供参考"
        description={
          "为了让你快速浏览,卡片上显示的「总收益 / 最大回撤 / 交易次数 / 胜率」是作者根据经验估算的**参考值**," +
          "并非当下跑出的真实数据。真实回测结果请点「用这个策略」后在回测页确认时间范围并点开始回测。\n\n" +
          "另外:**过去的表现不代表未来**。每个策略在你实际使用时,请结合当前市场环境、自己的风险承受能力和交易成本自行判断。"
        }
      />

      {/* 详情 Modal:完整描述 + 适合/不适合 */}
      <Modal
        open={detailCard !== null}
        title={detailCard?.display_name}
        onCancel={() => setDetailCard(null)}
        footer={[
          <Button key="close" onClick={() => setDetailCard(null)}>关闭</Button>,
          detailCard && (
            <Button
              key="use"
              type="primary"
              onClick={() => {
                setDetailCard(null);
                handleUse(detailCard);
              }}
            >
              用这个策略 →
            </Button>
          ),
        ]}
        width={640}
      >
        {detailCard && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Space>
              <Tag color={CATEGORY_COLOR[detailCard.category]}>{detailCard.category}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>{detailCard.code}</Text>
            </Space>

            <Paragraph style={{ fontSize: 14, whiteSpace: "pre-line", margin: 0 }}>
              {detailCard.long_description}
            </Paragraph>

            <div>
              <Text strong>✓ 适合</Text>
              <ul style={{ margin: "4px 0 0 0", paddingLeft: 20 }}>
                {detailCard.good_for.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>

            <div>
              <Text strong>✗ 不适合</Text>
              <ul style={{ margin: "4px 0 0 0", paddingLeft: 20 }}>
                {detailCard.bad_for.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>

            <div>
              <Text strong>参考回测表现({detailCard.preview.window})</Text>
              <Row gutter={16} style={{ marginTop: 8 }}>
                <Col span={6}>
                  <Text type="secondary" style={{ fontSize: 12 }}>总收益</Text>
                  <div>
                    <Text strong style={{ color: detailCard.preview.total_return_pct >= 0 ? RISE_COLOR : FALL_COLOR, fontSize: 18 }}>
                      {detailCard.preview.total_return_pct >= 0 ? "+" : ""}{detailCard.preview.total_return_pct.toFixed(1)}%
                    </Text>
                  </div>
                </Col>
                <Col span={6}>
                  <Text type="secondary" style={{ fontSize: 12 }}>最大回撤</Text>
                  <div>
                    <Text strong style={{ color: FALL_COLOR, fontSize: 18 }}>
                      {detailCard.preview.max_drawdown_pct.toFixed(1)}%
                    </Text>
                  </div>
                </Col>
                <Col span={6}>
                  <Text type="secondary" style={{ fontSize: 12 }}>交易次数</Text>
                  <div><Text strong style={{ fontSize: 18 }}>{detailCard.preview.total_trades}</Text></div>
                </Col>
                <Col span={6}>
                  <Text type="secondary" style={{ fontSize: 12 }}>胜率</Text>
                  <div><Text strong style={{ fontSize: 18 }}>{detailCard.preview.win_rate.toFixed(0)}%</Text></div>
                </Col>
              </Row>
              <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: "block" }}>
                * 参考值仅供浏览比较,真实数据请点「用这个策略」后在回测页自己跑。
              </Text>
            </div>
          </Space>
        )}
      </Modal>
    </Space>
  );
}


function StrategyCard({
  card,
  onUse,
  onDetail,
}: {
  card: StrategyGalleryCard;
  onUse: () => void;
  onDetail: () => void;
}) {
  const retSign = card.preview.total_return_pct >= 0 ? "+" : "";
  const retColor = card.preview.total_return_pct >= 0 ? RISE_COLOR : FALL_COLOR;

  return (
    <Card hoverable style={{ height: "100%" }} styles={{ body: { padding: 16 } }}>
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        {/* 分类 + 名称 */}
        <Space align="center" size={8}>
          <Tag color={CATEGORY_COLOR[card.category]} style={{ margin: 0 }}>{card.category}</Tag>
          <Text strong style={{ fontSize: 15 }}>{card.display_name}</Text>
        </Space>

        {/* 人话一句话 */}
        <Paragraph
          style={{ margin: 0, minHeight: 40, fontSize: 13, color: "#595959" }}
          ellipsis={{ rows: 2 }}
        >
          {card.one_liner}
        </Paragraph>

        {/* 参考回测数据 */}
        <Row gutter={4} style={{ background: "#fafafa", borderRadius: 4, padding: "8px 4px" }}>
          <Col span={8} style={{ textAlign: "center" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>总收益</Text>
            <div>
              <Text strong style={{ color: retColor }}>
                {retSign}{card.preview.total_return_pct.toFixed(1)}%
              </Text>
            </div>
          </Col>
          <Col span={8} style={{ textAlign: "center" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>最大回撤</Text>
            <div>
              <Text strong style={{ color: FALL_COLOR }}>
                {card.preview.max_drawdown_pct.toFixed(1)}%
              </Text>
            </div>
          </Col>
          <Col span={8} style={{ textAlign: "center" }}>
            <Text type="secondary" style={{ fontSize: 11 }}>交易次数</Text>
            <div><Text strong>{card.preview.total_trades}</Text></div>
          </Col>
        </Row>

        {/* 适合/不适合(取 1 条最具代表的) */}
        <div>
          {card.good_for[0] && (
            <Text style={{ fontSize: 12, color: "#52c41a", display: "block" }}>
              ✓ 适合:{card.good_for[0]}
            </Text>
          )}
          {card.bad_for[0] && (
            <Text style={{ fontSize: 12, color: "#ff7875", display: "block" }}>
              ✗ 不适合:{card.bad_for[0]}
            </Text>
          )}
        </div>

        {/* 操作按钮 */}
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <Button size="small" type="link" style={{ padding: 0 }} onClick={onDetail}>
            查看完整介绍
          </Button>
          <Button size="small" type="primary" onClick={onUse}>
            用这个策略 →
          </Button>
        </Space>
      </Space>
    </Card>
  );
}
