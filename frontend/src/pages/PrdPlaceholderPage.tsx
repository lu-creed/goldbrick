import { Card, Typography } from "antd";
import { Link } from "react-router-dom";

/**
 * 飞书 PRD 已规划、工程尚未实现的模块占位页。
 * title：菜单名；prdRef：文档中的版本/章节提示，便于你对照需求。
 */
export default function PrdPlaceholderPage({
  title,
  prdRef,
}: {
  title: string;
  prdRef: string;
}) {
  return (
    <Card style={{ maxWidth: 720, borderRadius: 12, borderColor: "#e2e8f0" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        {title}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        对应飞书《GoldBrick需求文档》{prdRef}。当前版本尚未开放此页，后续与指标计算、全市场扫描等能力一并接入。
      </Typography.Paragraph>
      <Link to="/">返回 K 线首页</Link>
    </Card>
  );
}
