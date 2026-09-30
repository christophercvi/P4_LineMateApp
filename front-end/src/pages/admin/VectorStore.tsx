import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Flex,
  Popconfirm,
  Progress,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ClearOutlined, DatabaseOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { IngestStage } from '@/api/types';
import type { VectorStoreView } from '@/api/views';
import { PageHeader } from '@/components/PageHeader';

const STAGE_COLOR: Record<IngestStage, string> = {
  queued: 'default',
  parsing: 'processing',
  embedding: 'processing',
  ready: 'success',
  failed: 'error',
};

export default function VectorStore() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({
    queryKey: ['vector-store'],
    queryFn: () => api<VectorStoreView>('/api/admin/vector-store'),
    refetchInterval: (query) =>
      query.state.data?.reindex ||
      query.state.data?.jobs.some((j) => j.stage !== 'ready' && j.stage !== 'failed')
        ? 1000
        : false,
  });
  const reindex = useMutation({
    mutationFn: (collection: string) =>
      api('/api/admin/vector-store/reindex', { body: { collection } }),
    onSuccess: () => {
      message.info('Re-index started');
      qc.invalidateQueries({ queryKey: ['vector-store'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });
  const reconcile = useMutation({
    mutationFn: () =>
      api<{ removed: number }>('/api/admin/vector-store/reconcile', { method: 'POST' }),
    onSuccess: (r) => {
      message.success(`Removed ${r.removed} orphan vector(s)`);
      qc.invalidateQueries({ queryKey: ['vector-store'] });
    },
  });
  const d = q.data;

  return (
    <>
      <PageHeader
        title="Vector store"
        subtitle="Chroma collections — one per embedding model, because vectors from different models are not comparable"
      />
      <Row gutter={[16, 16]}>
        {(d?.collections ?? []).map((c) => {
          const running = d?.reindex?.collection === c.name;
          return (
            <Col xs={24} lg={12} key={c.name}>
              <Card
                size="small"
                title={
                  <Flex gap={8} align="center">
                    <DatabaseOutlined />
                    <Typography.Text code>{c.name}</Typography.Text>
                  </Flex>
                }
                extra={
                  <Popconfirm
                    title={`Re-index ${c.name}?`}
                    description="Re-embeds every chunk. Answers keep using the old vectors until it finishes."
                    onConfirm={() => reindex.mutate(c.name)}
                  >
                    <Button
                      size="small"
                      icon={<ReloadOutlined />}
                      loading={running}
                      disabled={!!d?.reindex}
                    >
                      Re-index
                    </Button>
                  </Popconfirm>
                }
              >
                <Row gutter={8}>
                  <Col span={8}>
                    <Statistic title="Vectors" value={c.vectors} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="Documents" value={c.documents} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="Dimensions" value={c.dimensions} />
                  </Col>
                </Row>
                <Descriptions
                  size="small"
                  column={2}
                  style={{ marginTop: 12 }}
                  items={[
                    {
                      key: 'm',
                      label: 'Embedding model',
                      span: 2,
                      children: <Typography.Text code>{c.embeddingModel}</Typography.Text>,
                    },
                    {
                      key: 'r',
                      label: 'Runtime',
                      children: (
                        <Tag color={c.runtime === 'ollama' ? 'volcano' : 'geekblue'}>
                          {c.runtime}
                        </Tag>
                      ),
                    },
                    { key: 'd', label: 'Distance', children: c.distance },
                    {
                      key: 'l',
                      label: 'Last indexed',
                      children: dayjs(c.lastIndexed).format('MMM D, HH:mm'),
                    },
                    {
                      key: 'o',
                      label: 'Orphans',
                      children: c.orphaned ? <Tag color="warning">{c.orphaned}</Tag> : '0',
                    },
                  ]}
                />
                {running && (
                  <Progress
                    percent={d?.reindex?.progress ?? 0}
                    status="active"
                    style={{ marginTop: 8 }}
                  />
                )}
                {c.orphaned > 0 && !running && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginTop: 8 }}
                    title={`${c.orphaned} vectors belong to archived or replaced documents`}
                    action={
                      <Button
                        size="small"
                        icon={<ClearOutlined />}
                        loading={reconcile.isPending}
                        onClick={() => reconcile.mutate()}
                      >
                        Reconcile
                      </Button>
                    }
                  />
                )}
              </Card>
            </Col>
          );
        })}
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card size="small" title="Ingestion jobs">
            <Table
              rowKey="id"
              size="small"
              loading={q.isLoading}
              dataSource={d?.jobs}
              pagination={{ pageSize: 6 }}
              columns={[
                { title: 'Job', dataIndex: 'id', width: 90 },
                { title: 'File', dataIndex: 'fileName', ellipsis: true },
                {
                  title: 'Collection',
                  dataIndex: 'collection',
                  ellipsis: true,
                  render: (c: string) => (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {c}
                    </Typography.Text>
                  ),
                },
                {
                  title: 'Stage',
                  dataIndex: 'stage',
                  render: (s: IngestStage, j) =>
                    s === 'parsing' || s === 'embedding' ? (
                      <Progress
                        size="small"
                        percent={j.progress}
                        style={{ width: 120 }}
                        format={() => s}
                      />
                    ) : (
                      <Badge status={STAGE_COLOR[s] as 'default'} text={s} />
                    ),
                },
                {
                  title: 'Chunks',
                  dataIndex: 'chunks',
                  align: 'right',
                  render: (c?: number) => c ?? '—',
                },
                { title: 'By', dataIndex: 'requestedByName' },
                {
                  title: 'Error',
                  dataIndex: 'error',
                  ellipsis: true,
                  render: (e?: string) =>
                    e ? <Typography.Text type="danger">{e}</Typography.Text> : null,
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card size="small" title="Service health">
            <Descriptions
              size="small"
              column={1}
              items={(d?.services ?? []).map((s) => ({
                key: s.name,
                label: (
                  <Badge
                    status={
                      s.status === 'up' ? 'success' : s.status === 'degraded' ? 'warning' : 'error'
                    }
                    text={s.name}
                  />
                ),
                children: (
                  <Flex vertical>
                    <span>{s.detail}</span>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {s.version}
                    </Typography.Text>
                  </Flex>
                ),
              }))}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}
