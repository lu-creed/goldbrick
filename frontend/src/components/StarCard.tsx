/**
 * 四维星级卡片:显示一个维度的得分(1-5 星)+ 维度名 + 简短说明。
 *
 * 用于回测结果页的「收益性 / 风险控制 / 稳定性 / 交易频率」四个评价。
 * 星级实心 vs 空心表示得分,颜色用金黄色(满分感) + 灰色(未达)。
 */
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { Space, Tooltip, Typography } from "antd";

const { Text } = Typography;

interface Props {
  title: string;            // 如「收益性」
  stars: number;            // 1-5
  hint?: string;            // hover 的说明文字:告诉用户这颗星怎么来的
}

export default function StarCard({ title, stars, hint }: Props) {
  const clamped = Math.max(1, Math.min(5, Math.round(stars)));
  const content = (
    <div
      style={{
        padding: "8px 12px",
        border: "1px solid #f0f0f0",
        borderRadius: 6,
        background: "#fafafa",
        minHeight: 64,
      }}
    >
      <Text style={{ fontSize: 12, color: "#8c8c8c" }}>{title}</Text>
      <div style={{ marginTop: 4 }}>
        <Space size={2}>
          {[1, 2, 3, 4, 5].map((i) =>
            i <= clamped ? (
              <StarFilled key={i} style={{ color: "#faad14", fontSize: 16 }} />
            ) : (
              <StarOutlined key={i} style={{ color: "#d9d9d9", fontSize: 16 }} />
            ),
          )}
        </Space>
      </div>
    </div>
  );

  return hint ? <Tooltip title={hint}>{content}</Tooltip> : content;
}
