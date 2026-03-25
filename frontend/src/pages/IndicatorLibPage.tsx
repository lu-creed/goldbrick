import { ArrowLeftOutlined } from "@ant-design/icons";
import { Button, Card, Descriptions, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import {
  fetchIndicatorDetail,
  fetchIndicators,
  type IndicatorDetail,
  type IndicatorListItem,
} from "../api/client";

export default function IndicatorLibPage() {
  const [list, setList] = useState<IndicatorListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<IndicatorDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        setList(await fetchIndicators());
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const openDetail = async (id: number) => {
    setDetailLoading(true);
    try {
      setDetail(await fetchIndicatorDetail(id));
    } finally {
      setDetailLoading(false);
    }
  };

  const listColumns: ColumnsType<IndicatorListItem> = [
    { title: "指标名称", dataIndex: "display_name", width: 160,
      render: (v, r) => <Typography.Link onClick={() => void openDetail(r.id)}>{v}</Typography.Link> },
    { title: "英文标识", dataIndex: "name", width: 120,
      render: (v: string) => <Tag>{v}</Tag> },
    { title: "描述", dataIndex: "description", ellipsis: true },
    { title: "参数数", dataIndex: "params_count", width: 80, align: "center" },
    { title: "子指标数", dataIndex: "sub_count", width: 90, align: "center" },
    {
      title: "操作", key: "action", width: 80, align: "center",
      render: (_, r) => <Button size="small" onClick={() => void openDetail(r.id)}>详情</Button>,
    },
  ];

  if (detail) {
    const paramCols: ColumnsType<IndicatorDetail["params"][0]> = [
      { title: "参数名", dataIndex: "name", width: 120 },
      { title: "说明", dataIndex: "description", ellipsis: true },
      { title: "默认值", dataIndex: "default_value", width: 100,
        render: (v: string | null) => v ?? "-" },
    ];
    const subCols: ColumnsType<IndicatorDetail["sub_indicators"][0]> = [
      { title: "子指标名", dataIndex: "name", width: 160 },
      { title: "说明", dataIndex: "description", ellipsis: true },
    ];

    return (
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => setDetail(null)}>返回列表</Button>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {detail.display_name}（{detail.name}）
          </Typography.Title>
        </Space>

        <Card title="基本信息">
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="指标名称">{detail.display_name}</Descriptions.Item>
            <Descriptions.Item label="英文标识"><Tag>{detail.name}</Tag></Descriptions.Item>
            <Descriptions.Item label="描述">{detail.description ?? "-"}</Descriptions.Item>
          </Descriptions>
        </Card>

        <Card title={`参数信息（共 ${detail.params.length} 个）`}>
          {detail.params.length === 0
            ? <Typography.Text type="secondary">该指标无参数</Typography.Text>
            : <Table rowKey="id" columns={paramCols} dataSource={detail.params} pagination={false} size="small" />}
        </Card>

        <Card title={`子指标（共 ${detail.sub_indicators.length} 个）`}>
          <Table rowKey="id" columns={subCols} dataSource={detail.sub_indicators} pagination={false} size="small" />
        </Card>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>指标库</Typography.Title>
      <Card loading={detailLoading}>
        <Table
          rowKey="id"
          loading={loading}
          columns={listColumns}
          dataSource={list}
          pagination={false}
          size="middle"
          onRow={(r) => ({ onClick: () => void openDetail(r.id), style: { cursor: "pointer" } })}
        />
      </Card>
    </Space>
  );
}
