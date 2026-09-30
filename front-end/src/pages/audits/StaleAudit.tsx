import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Flex,
  InputNumber,
  Row,
  Skeleton,
  Slider,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { Bar } from '@ant-design/charts';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { StaleRow } from '@/api/types';
import type { StaleAuditView } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { useUi } from '@/auth/store';
import { can, denyReason } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { CategoryTag, FreshnessTag, Person, StationTag } from '@/components/tags';

export default function StaleAudit() {
  const actor = useActor();
  const mode = useUi((s) => s.mode);
  const qc = useQueryClient();
  const { message } = App.useApp();
  const [threshold, setThreshold] = useState(180);
  const [selected, setSelected] = useState<string[]>([]);
  const q = useQuery({
    queryKey: ['audit-stale', threshold],
    queryFn: () => api<StaleAuditView>('/api/audits/stale', { query: { threshold } }),
  });

  const bulk = useMutation({
    mutationFn: (ids: string[]) =>
      api<{ updated: string[]; skipped: string[] }>('/api/documents/review-bulk', {
        body: { ids },
      }),
    onSuccess: (r) => {
      message.success(
        `${r.updated.length} document${r.updated.length === 1 ? '' : 's'} marked reviewed${r.skipped.length ? ` · ${r.skipped.length} skipped (outside your station)` : ''}`,
      );
      setSelected([]);
      qc.invalidateQueries({ queryKey: ['audit-stale'] });
      qc.invalidateQueries({ queryKey: ['documents'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });

  if (!q.data) return <Skeleton active paragraph={{ rows: 12 }} />;
  const v = q.data;
  const act = can.actOnAudits(actor);
  const chartData = [...v.all]
    .sort((a, b) => b.days - a.days)
    .map((d) => ({
      ...d,
      label: `${d.docId}`,
      state: d.days > threshold ? 'Stale' : 'Within policy',
    }));

  return (
    <>
      <PageHeader
        title="Stale-document audit"
        subtitle={`${v.scope} · documents not reviewed in more than ${threshold} days · Incident Reports are excluded`}
        extra={
          <Tooltip
            title={
              act ? '' : 'Admins can view the audit but reviews are signed off by kitchen leads.'
            }
          >
            <Button
              type="primary"
              icon={<SafetyCertificateOutlined />}
              disabled={!act || selected.length === 0}
              loading={bulk.isPending}
              onClick={() => bulk.mutate(selected)}
            >
              Mark {selected.length || ''} reviewed
            </Button>
          </Tooltip>
        }
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card style={{ height: '100%' }}>
            <Typography.Text type="secondary">Review threshold (days)</Typography.Text>
            <Flex gap={12} align="center" style={{ marginTop: 8 }}>
              <Slider
                min={30}
                max={365}
                step={5}
                value={threshold}
                onChange={setThreshold}
                style={{ flex: 1 }}
                marks={{ 90: '90', 180: '180', 365: '365' }}
              />
              <InputNumber
                min={30}
                max={365}
                value={threshold}
                onChange={(n) => n && setThreshold(n)}
                style={{ width: 80 }}
              />
            </Flex>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: 0 }}>
              Policy: every Recipe, SOP and Onboarding document is re-reviewed within 180 days.
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col xs={12} md={4}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="Stale"
              value={v.rows.length}
              styles={{ content: { color: v.rows.length ? '#CF1322' : undefined } }}
            />
          </Card>
        </Col>
        <Col xs={12} md={4}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="Cited this week"
              value={v.rows.reduce((s, r) => s + r.citedLast7d, 0)}
              suffix="×"
            />
          </Card>
        </Col>
        <Col xs={12} md={4}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="Linked open tickets"
              value={v.rows.reduce((s, r) => s + r.openTickets, 0)}
            />
          </Card>
        </Col>
        <Col xs={12} md={4}>
          <Card style={{ height: '100%' }}>
            <Statistic title="Incidents excluded" value={v.excludedIncidents.length} />
          </Card>
        </Col>
      </Row>
      <Flex vertical gap={8} style={{ margin: '16px 0' }}>
        {v.excludedIncidents.length > 0 && (
          <Alert
            type="info"
            showIcon
            title={`${v.excludedIncidents.length} Incident Reports are older than ${threshold} days but excluded`}
            description={`Incident Reports are historical records, not procedures: ${v.excludedIncidents.map((d) => `${d.docId} (${d.days} d)`).join(', ')}.`}
          />
        )}
        {v.boundary.length > 0 && (
          <Alert
            type="warning"
            showIcon
            title={`Boundary case: ${v.boundary.map((b) => b.docId).join(', ')} is exactly ${threshold} days old`}
            description={`The rule is “more than ${threshold} days”, so it is not stale yet — it will be tomorrow.`}
          />
        )}
      </Flex>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card title={`Stale documents (${v.rows.length})`} styles={{ body: { padding: 0 } }}>
            <Table<StaleRow>
              rowKey="docId"
              size="middle"
              dataSource={v.rows}
              pagination={false}
              scroll={{ x: 820 }}
              rowSelection={
                act
                  ? {
                      selectedRowKeys: selected,
                      onChange: (k) => setSelected(k as string[]),
                      getCheckboxProps: (r) => ({
                        disabled: actor.role === 'sous_chef' && r.station !== actor.station,
                        title: denyReason('review', actor),
                      }),
                    }
                  : undefined
              }
              columns={[
                {
                  title: 'Document',
                  dataIndex: 'title',
                  render: (_t, r) => (
                    <Flex vertical>
                      <Link to={`/documents/${r.docId}`}>{r.title}</Link>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {r.docId}
                      </Typography.Text>
                    </Flex>
                  ),
                },
                {
                  title: 'Category',
                  dataIndex: 'category',
                  width: 120,
                  render: (c) => <CategoryTag value={c} />,
                },
                {
                  title: 'Station',
                  dataIndex: 'station',
                  width: 140,
                  render: (s) => <StationTag value={s} />,
                },
                {
                  title: 'Owner',
                  dataIndex: 'ownerId',
                  width: 160,
                  render: (o) => <Person id={o} />,
                },
                {
                  title: 'Last reviewed',
                  dataIndex: 'daysSinceReview',
                  width: 150,
                  defaultSortOrder: 'descend',
                  sorter: (a, b) => a.daysSinceReview - b.daysSinceReview,
                  render: (d, r) => (
                    <Tooltip title={dayjs(r.lastReviewed).format('D MMM YYYY')}>
                      <span>
                        <FreshnessTag days={d} />
                      </span>
                    </Tooltip>
                  ),
                },
                {
                  title: 'Cited 7 d',
                  dataIndex: 'citedLast7d',
                  width: 90,
                  align: 'right',
                  render: (n) => (n ? <Tag color="volcano">{n}×</Tag> : 0),
                },
                { title: 'Tickets', dataIndex: 'openTickets', width: 80, align: 'right' },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card
            title="Days since last review"
            extra={<Tag color="error">— {threshold}-day policy</Tag>}
          >
            <Bar
              data={chartData}
              xField="label"
              yField="days"
              colorField="state"
              height={Math.max(320, chartData.length * 22)}
              theme={mode === 'dark' ? 'classicDark' : 'classic'}
              scale={{
                color: { domain: ['Stale', 'Within policy'], range: ['#CF1322', '#8FB573'] },
              }}
              axis={{ x: { title: false }, y: { title: 'days' } }}
              legend={{ color: { position: 'top' } }}
              tooltip={{ items: [{ channel: 'y', name: 'Days' }] }}
              annotations={[
                {
                  type: 'lineY',
                  data: [threshold],
                  style: { stroke: '#CF1322', lineDash: [4, 4], lineWidth: 1.5 },
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}
