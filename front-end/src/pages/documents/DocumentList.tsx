import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  App,
  Button,
  Card,
  Dropdown,
  Flex,
  Input,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from 'antd';
import {
  CloudUploadOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  FileSyncOutlined,
  LoadingOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { FileCard } from '@ant-design/x';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { IngestJob, LmDocument } from '@/api/types';
import type { DocListItem } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { can, denyReason } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { CategoryTag, FreshnessTag, Person, SourceTag, StationTag } from '@/components/tags';
import { DocumentUploadDrawer } from '@/components/DocumentUploadDrawer';
import { IngestSteps } from '@/components/IngestSteps';
import { CATEGORIES, CATEGORY, FILE_ICON } from '@/theme/tokens';
import { stations } from '@/api/lookups';

type ListData = { items: DocListItem[]; hiddenIncidents: number };

export default function DocumentList() {
  const actor = useActor();
  const { message, modal } = App.useApp();
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [category, setCategory] = useState<string | undefined>();
  const [station, setStation] = useState<string | undefined>();
  const [source, setSource] = useState<'all' | 'seed' | 'upload'>('all');
  const [staleOnly, setStaleOnly] = useState(false);
  const [drawer, setDrawer] = useState<{ open: boolean; replace: DocListItem | null }>({
    open: false,
    replace: null,
  });
  const key = ['documents', { q, category, station, source, staleOnly }];

  const docs = useQuery({
    queryKey: key,
    queryFn: () =>
      api<ListData>('/api/documents', {
        query: {
          q,
          category,
          station,
          source: source === 'all' ? undefined : source,
          stale: staleOnly || undefined,
        },
      }),
  });
  const jobs = useQuery({
    queryKey: ['ingest-jobs'],
    queryFn: () => api<IngestJob[]>('/api/ingest/jobs'),
    refetchInterval: (query) =>
      query.state.data?.some((j) => j.stage !== 'ready' && j.stage !== 'failed') ? 900 : 4000,
  });
  const active = (jobs.data ?? [])
    .filter(
      (j) =>
        (j.stage !== 'ready' && j.stage !== 'failed') ||
        (j.finishedAt && dayjs().diff(j.finishedAt, 'second') < 30),
    )
    .slice(0, 3);

  const review = useMutation({
    mutationFn: (id: string) => api<DocListItem>(`/api/documents/${id}/review`, { body: {} }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<ListData>(key);
      if (prev)
        qc.setQueryData<ListData>(key, {
          ...prev,
          items: prev.items.map((d) =>
            d.id === id ? { ...d, stale: false, daysSinceReview: 0 } : d,
          ),
        });
      return { prev };
    },
    onError: (e, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
      message.error(e instanceof ApiError ? e.message : 'Could not mark reviewed');
    },
    onSuccess: (d) => message.success(`${d.id} marked reviewed today`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  });
  const archive = useMutation({
    mutationFn: (id: string) => api<DocListItem>(`/api/documents/${id}/archive`, { body: {} }),
    onSuccess: (d) => {
      message.success(`${d.id} archived — removed from retrieval`);
      qc.invalidateQueries({ queryKey: ['documents'] });
    },
  });

  const columns: TableColumnsType<DocListItem> = [
    {
      title: 'Document',
      dataIndex: 'title',
      key: 'title',
      width: 360,
      render: (_v, d) => (
        <Flex gap={10} align="center">
          <FileCard
            name={d.fileName}
            icon={FILE_ICON[d.fileKind]}
            size="small"
            styles={{
              root: { width: 36, padding: 0, border: 0, background: 'transparent' },
              name: { display: 'none' },
              description: { display: 'none' },
            }}
          />
          <Flex vertical style={{ minWidth: 0 }}>
            <Link to={`/documents/${d.id}`}>
              <Typography.Text strong ellipsis style={{ maxWidth: 280 }}>
                {d.title}
              </Typography.Text>
            </Link>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {d.id} · {d.fileName}
            </Typography.Text>
          </Flex>
        </Flex>
      ),
    },
    {
      title: 'Category',
      dataIndex: 'category',
      key: 'category',
      width: 140,
      render: (v) => <CategoryTag value={v} />,
    },
    {
      title: 'Station',
      dataIndex: 'station',
      key: 'station',
      width: 150,
      render: (v) => <StationTag value={v} />,
    },
    {
      title: 'Owner',
      dataIndex: 'ownerId',
      key: 'owner',
      width: 170,
      render: (v) => <Person id={v} />,
    },
    {
      title: 'Last reviewed',
      dataIndex: 'daysSinceReview',
      key: 'reviewed',
      width: 160,
      sorter: (a, b) => a.daysSinceReview - b.daysSinceReview,
      render: (v, d) =>
        d.category === 'incident' ? (
          <Tooltip title="Incident Reports are historical records and are excluded from the review policy">
            <Tag>{dayjs(d.lastReviewed).format('D MMM YYYY')}</Tag>
          </Tooltip>
        ) : (
          <FreshnessTag days={v} />
        ),
    },
    {
      title: 'Source',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (v) => <SourceTag value={v} />,
    },
    {
      title: 'Index',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (v: LmDocument['status'], d) =>
        v === 'processing' ? (
          <Tag icon={<LoadingOutlined />} color="processing">
            Ingesting
          </Tag>
        ) : v === 'failed' ? (
          <Tag color="error">Failed</Tag>
        ) : v === 'archived' ? (
          <Tag>Archived</Tag>
        ) : (
          <Tag color="success" variant="outlined">
            {d.chunkCount} chunks
          </Tag>
        ),
    },
    {
      title: 'Open tickets',
      dataIndex: 'openTickets',
      key: 'openTickets',
      width: 110,
      align: 'right',
      render: (v) =>
        v ? <Tag color="orange">{v}</Tag> : <Typography.Text type="secondary">0</Typography.Text>,
    },
    {
      title: '',
      key: 'actions',
      width: 56,
      fixed: 'right',
      render: (_v, d) => {
        const asDoc = d as unknown as LmDocument;
        const canReplace = can.replaceDocument(actor, asDoc);
        const canReview = can.markReviewed(actor, asDoc) && d.category !== 'incident';
        const canArchive = can.archiveDocument(actor) && d.status !== 'archived';
        return (
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                {
                  key: 'replace',
                  icon: <FileSyncOutlined />,
                  disabled: !canReplace,
                  label: canReplace ? (
                    'Replace file'
                  ) : (
                    <Tooltip title={denyReason('upload', actor)} placement="left">
                      Replace file
                    </Tooltip>
                  ),
                },
                {
                  key: 'review',
                  icon: <SafetyCertificateOutlined />,
                  disabled: !canReview,
                  label: canReview ? (
                    'Mark reviewed'
                  ) : (
                    <Tooltip
                      title={
                        d.category === 'incident'
                          ? 'Incident Reports are not reviewed'
                          : denyReason('review', actor)
                      }
                      placement="left"
                    >
                      Mark reviewed
                    </Tooltip>
                  ),
                },
                { type: 'divider' },
                {
                  key: 'archive',
                  icon: <DeleteOutlined />,
                  danger: true,
                  disabled: !canArchive,
                  label: canArchive ? (
                    'Archive'
                  ) : (
                    <Tooltip title={denyReason('archive', actor)} placement="left">
                      Archive
                    </Tooltip>
                  ),
                },
              ],
              onClick: ({ key: k }) => {
                if (k === 'replace') setDrawer({ open: true, replace: d });
                if (k === 'review') review.mutate(d.id);
                if (k === 'archive')
                  modal.confirm({
                    title: `Archive ${d.id}?`,
                    content: 'It stays in the audit trail but is removed from search and answers.',
                    okText: 'Archive',
                    okButtonProps: { danger: true },
                    onOk: () => archive.mutateAsync(d.id),
                  });
              },
            }}
          >
            <Button type="text" aria-label={`Actions for ${d.id}`} icon={<EllipsisOutlined />} />
          </Dropdown>
        );
      },
    },
  ];

  const items = docs.data?.items ?? [];
  const staleCount = items.filter((d) => d.stale).length;
  const uploads = items.filter((d) => d.source === 'upload').length;

  return (
    <>
      <PageHeader
        title="Documents"
        subtitle={`Knowledge base · ${items.length} documents (${items.length - uploads} seed, ${uploads} uploaded) · ${staleCount} stale`}
        extra={
          <Tooltip title={can.uploadAny(actor) ? '' : denyReason('upload', actor)}>
            <Button
              type="primary"
              icon={<CloudUploadOutlined />}
              disabled={!can.uploadAny(actor)}
              onClick={() => setDrawer({ open: true, replace: null })}
            >
              Upload document
            </Button>
          </Tooltip>
        }
      />
      {active.length > 0 && (
        <Card size="small" title="Ingestion" style={{ marginBottom: 16 }}>
          <Flex vertical gap={16}>
            {active.map((j) => (
              <div key={j.id}>
                <Typography.Text strong>{j.fileName}</Typography.Text>{' '}
                <Typography.Text type="secondary">
                  · <Link to={`/documents/${j.docId}`}>{j.docId}</Link> · {j.collection}
                </Typography.Text>
                <IngestSteps job={j} compact />
              </div>
            ))}
          </Flex>
        </Card>
      )}
      <Card styles={{ body: { padding: 0 } }}>
        <Flex gap={12} wrap style={{ padding: 16 }} align="center">
          <Input.Search
            placeholder="Search title, tag, summary…"
            allowClear
            style={{ width: 280 }}
            onSearch={setQ}
            onChange={(e) => !e.target.value && setQ('')}
          />
          <Select
            allowClear
            placeholder="Category"
            style={{ width: 170 }}
            value={category}
            onChange={setCategory}
            options={CATEGORIES.map((c) => ({
              value: c,
              label: CATEGORY[c].label,
              disabled: c === 'incident' && !can.seeIncidentReports(actor),
            }))}
          />
          <Select
            allowClear
            placeholder="Station"
            style={{ width: 170 }}
            value={station}
            onChange={setStation}
            options={stations().map((s) => ({ value: s.id, label: s.name }))}
          />
          <Segmented
            value={source}
            onChange={(v) => setSource(v as typeof source)}
            options={[
              { label: 'All', value: 'all' },
              { label: 'Seed', value: 'seed' },
              { label: 'Uploaded', value: 'upload' },
            ]}
          />
          <Space>
            <Switch
              size="small"
              checked={staleOnly}
              onChange={setStaleOnly}
              aria-label="Stale only"
            />
            <Typography.Text>Stale only (&gt; 180 days)</Typography.Text>
          </Space>
        </Flex>
        <Table<DocListItem>
          rowKey="id"
          size="middle"
          loading={docs.isLoading}
          columns={columns}
          dataSource={items}
          scroll={{ x: 1360 }}
          pagination={{ pageSize: 12, showSizeChanger: false, showTotal: (t) => `${t} documents` }}
        />
      </Card>
      <DocumentUploadDrawer
        open={drawer.open}
        replace={drawer.replace}
        onClose={() => setDrawer({ open: false, replace: null })}
      />
    </>
  );
}
