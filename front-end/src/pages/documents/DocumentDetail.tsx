import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Flex,
  Result,
  Row,
  Skeleton,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import { FileSyncOutlined, MessageOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { FileCard } from '@ant-design/x';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { LmDocument } from '@/api/types';
import type { DocListItem, DocumentDetailView } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { can, denyReason } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { ListRow, RowList } from '@/components/ListRow';
import {
  CategoryTag,
  FreshnessTag,
  Person,
  PriorityTag,
  SourceTag,
  StationTag,
  StatusTag,
} from '@/components/tags';
import { Md } from '@/components/Md';
import { DocumentUploadDrawer } from '@/components/DocumentUploadDrawer';
import { IngestSteps } from '@/components/IngestSteps';
import { FILE_ICON, STALE_DAYS } from '@/theme/tokens';
import { crewById } from '@/api/lookups';

export default function DocumentDetail() {
  const { id = '' } = useParams();
  const actor = useActor();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [replaceOpen, setReplaceOpen] = useState(false);

  const q = useQuery({
    queryKey: ['document', id],
    queryFn: () => api<DocumentDetailView>(`/api/documents/${id}`),
    refetchInterval: (query) =>
      query.state.data?.jobs.some((j) => j.stage !== 'ready' && j.stage !== 'failed') ? 900 : false,
  });
  const review = useMutation({
    mutationFn: () =>
      api(`/api/documents/${id}/review`, {
        body: { note: 'Reviewed from document page — no changes needed' },
      }),
    onSuccess: () => {
      message.success(`${id} marked reviewed today`);
      qc.invalidateQueries({ queryKey: ['document', id] });
      qc.invalidateQueries({ queryKey: ['documents'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });

  if (q.isLoading) return <Skeleton active paragraph={{ rows: 14 }} />;
  if (q.error || !q.data) {
    const forbidden = q.error instanceof ApiError && q.error.status === 403;
    return (
      <Result
        status={forbidden ? '403' : '404'}
        title={forbidden ? 'Restricted document' : 'Document not found'}
        subTitle={q.error?.message}
        extra={<Button onClick={() => navigate('/documents')}>Back to documents</Button>}
      />
    );
  }
  const { document: d, chunks, relatedTickets, jobs, embeddingModel } = q.data;
  const asDoc = d as LmDocument;
  const canReplace = can.replaceDocument(actor, asDoc);
  const canReview = can.markReviewed(actor, asDoc) && d.category !== 'incident';
  const activeJob =
    jobs.find((j) => j.stage !== 'ready' && j.stage !== 'failed') ??
    (jobs[0]?.finishedAt && dayjs().diff(jobs[0].finishedAt, 'second') < 30 ? jobs[0] : undefined);
  const { body: _body, ...rest } = d;
  void _body;
  const listItem: DocListItem = {
    ...rest,
    openTickets: relatedTickets.filter((t) => ['open', 'in_progress', 'blocked'].includes(t.status))
      .length,
  };

  return (
    <>
      <PageHeader
        crumbs={[{ title: 'Documents', to: '/documents' }, { title: d.id }]}
        title={d.title}
        tags={
          <>
            <CategoryTag value={d.category} />
            <StationTag value={d.station} />
            {d.category !== 'incident' && <FreshnessTag days={d.daysSinceReview} />}
            <SourceTag value={d.source} />
          </>
        }
        subtitle={d.summary}
        extra={
          <>
            {can.ask(actor) && (
              <Button
                icon={<MessageOutlined />}
                onClick={() => navigate(`/ask?q=${encodeURIComponent(`Summarise ${d.title}`)}`)}
              >
                Ask about this
              </Button>
            )}
            <Tooltip title={canReplace ? '' : denyReason('upload', actor)}>
              <Button
                icon={<FileSyncOutlined />}
                disabled={!canReplace}
                onClick={() => setReplaceOpen(true)}
              >
                Replace file
              </Button>
            </Tooltip>
            <Tooltip
              title={
                canReview
                  ? ''
                  : d.category === 'incident'
                    ? 'Incident Reports are not reviewed'
                    : denyReason('review', actor)
              }
            >
              <Button
                type="primary"
                icon={<SafetyCertificateOutlined />}
                disabled={!canReview}
                loading={review.isPending}
                onClick={() => review.mutate()}
              >
                Mark reviewed
              </Button>
            </Tooltip>
          </>
        }
      />
      {d.stale && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title={`Last reviewed ${d.daysSinceReview} days ago — over the ${STALE_DAYS}-day review policy`}
          description={`Answers that cite this document show a staleness warning.${d.citedLast7d ? ` It was cited ${d.citedLast7d}× in answers in the last 7 days.` : ''} ${crewById(d.ownerId)?.name ?? 'The owner'} is responsible for the review.`}
        />
      )}
      {d.status === 'failed' && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title="Ingestion failed"
          description="The file could not be parsed. Replace it with a readable copy to index it."
        />
      )}
      {activeJob && (
        <Card size="small" title={`Ingestion · ${activeJob.id}`} style={{ marginBottom: 16 }}>
          <IngestSteps job={activeJob} />
        </Card>
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={16}>
          <Card
            title="Content"
            extra={
              <Typography.Text type="secondary">
                v{d.version} · rendered with XMarkdown
              </Typography.Text>
            }
          >
            <Md content={d.body} />
            <Typography.Paragraph
              type="secondary"
              style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}
            >
              Illustrative content — verify against your local health code.
            </Typography.Paragraph>
          </Card>
          <Card
            title="Vector store chunks"
            style={{ marginTop: 16 }}
            extra={<Tag>{embeddingModel} · 768-d · cosine</Tag>}
          >
            <Table
              rowKey="index"
              size="small"
              pagination={false}
              dataSource={chunks}
              scroll={{ x: 760 }}
              columns={[
                { title: '#', dataIndex: 'index', width: 50 },
                { title: 'Section', dataIndex: 'heading', width: 200, ellipsis: true },
                { title: 'Tokens', dataIndex: 'tokens', width: 80, align: 'right' },
                { title: 'Preview', dataIndex: 'preview', ellipsis: true },
                {
                  title: 'Content SHA',
                  dataIndex: 'sha',
                  width: 150,
                  render: (v: string) => (
                    <Typography.Text code copyable>
                      {v}
                    </Typography.Text>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Flex vertical gap={16}>
            <Card title="Details">
              <Descriptions
                column={1}
                size="small"
                items={[
                  { key: 'owner', label: 'Owner', children: <Person id={d.ownerId} showTitle /> },
                  { key: 'station', label: 'Station', children: <StationTag value={d.station} /> },
                  {
                    key: 'created',
                    label: 'Created',
                    children: dayjs(d.createdAt).format('D MMM YYYY'),
                  },
                  {
                    key: 'reviewed',
                    label: 'Last reviewed',
                    children: `${dayjs(d.lastReviewed).format('D MMM YYYY')} (${d.daysSinceReview} d)`,
                  },
                  { key: 'version', label: 'Version', children: `v${d.version}` },
                  {
                    key: 'tags',
                    label: 'Tags',
                    children: (
                      <Flex gap={4} wrap>
                        {d.tags.map((t) => (
                          <Tag key={t}>{t}</Tag>
                        ))}
                      </Flex>
                    ),
                  },
                ]}
              />
              <Typography.Text type="secondary" style={{ display: 'block', margin: '12px 0 8px' }}>
                Original file
              </Typography.Text>
              <FileCard
                name={d.fileName}
                byte={d.fileBytes}
                icon={FILE_ICON[d.fileKind]}
                description={`${d.source === 'seed' ? 'Seed pack' : 'Uploaded'} · ${d.chunkCount} chunks`}
              />
            </Card>
            <Card title="Review history">
              <Timeline
                items={d.reviewHistory.map((r, i) => ({
                  color: i === 0 ? 'green' : 'gray',
                  title: (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {dayjs(r.at).format('D MMM YYYY')}
                    </Typography.Text>
                  ),
                  content: (
                    <>
                      <Typography.Text strong>{crewById(r.by)?.name ?? r.by}</Typography.Text>
                      <br />
                      <Typography.Text type="secondary">{r.note}</Typography.Text>
                    </>
                  ),
                }))}
              />
            </Card>
            <Card title={`Related tickets (${relatedTickets.length})`}>
              <RowList
                items={relatedTickets}
                rowKey={(t) => t.id}
                empty={
                  <Typography.Text type="secondary">
                    No tickets reference this document
                  </Typography.Text>
                }
                render={(t) => (
                  <ListRow
                    onClick={() => navigate(`/tickets/${t.id}`)}
                    title={
                      <Flex gap={6} wrap align="center">
                        <Typography.Text strong>{t.id}</Typography.Text>
                        <PriorityTag value={t.priority} />
                        <StatusTag value={t.status} />
                      </Flex>
                    }
                    description={t.title}
                  />
                )}
              />
            </Card>
          </Flex>
        </Col>
      </Row>
      <DocumentUploadDrawer
        open={replaceOpen}
        replace={listItem}
        onClose={() => setReplaceOpen(false)}
      />
    </>
  );
}
