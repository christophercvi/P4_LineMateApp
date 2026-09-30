import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Flex,
  Grid,
  Row,
  Skeleton,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { ArrowRightOutlined, SwapOutlined, WarningOutlined } from '@ant-design/icons';
import { Sankey } from '@ant-design/charts';
import { api, ApiError } from '@/api/client';
import type { MismatchRow } from '@/api/types';
import type { OwnershipView } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { useUi } from '@/auth/store';
import { can, denyReason } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { Person, PriorityTag, StationTag, StatusTag } from '@/components/tags';

export default function OwnershipAudit() {
  const screens = Grid.useBreakpoint();
  const actor = useActor();
  const mode = useUi((s) => s.mode);
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({
    queryKey: ['audit-ownership'],
    queryFn: () => api<OwnershipView>('/api/audits/ownership'),
  });
  const reassign = useMutation({
    mutationFn: (r: MismatchRow) =>
      api(`/api/tickets/${r.ticketId}`, {
        method: 'PATCH',
        body: { assigneeId: r.suggestedAssigneeId },
      }),
    onSuccess: (_d, r) => {
      message.success(`${r.ticketId} reassigned`);
      qc.invalidateQueries({ queryKey: ['audit-ownership'] });
      qc.invalidateQueries({ queryKey: ['tickets'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });

  if (!q.data) return <Skeleton active paragraph={{ rows: 12 }} />;
  const v = q.data;
  const canAssign = (r: MismatchRow) =>
    actor.role === 'kitchen_manager' ||
    (actor.role === 'sous_chef' && actor.station === r.docStation);

  return (
    <>
      <PageHeader
        title="Ownership audit"
        subtitle={`${v.scope} · open tickets whose assignee works outside the station that owns the linked SOP or recipe`}
      />
      {v.rows.some((r) => r.docStale && r.priority === 'critical') && (
        <Alert
          type="error"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 16 }}
          title="Combined risk: a Critical ticket is assigned outside its owning station and linked to a stale SOP"
          description={v.rows
            .filter((r) => r.docStale && r.priority === 'critical')
            .map((r) => `${r.ticketId} → ${r.docId}`)
            .join(' · ')}
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card title={`Open mismatches (${v.rows.length})`} styles={{ body: { padding: 0 } }}>
            <Table<MismatchRow>
              rowKey="ticketId"
              size="middle"
              pagination={false}
              dataSource={v.rows}
              scroll={{ x: 980 }}
              columns={[
                {
                  title: 'Ticket',
                  dataIndex: 'title',
                  render: (_t, r) => (
                    <Flex vertical>
                      <Link to={`/tickets/${r.ticketId}`}>{r.title}</Link>
                      <Flex gap={6}>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {r.ticketId}
                        </Typography.Text>
                        <StatusTag value={r.status} />
                      </Flex>
                    </Flex>
                  ),
                },
                {
                  title: 'Priority',
                  dataIndex: 'priority',
                  width: 100,
                  render: (p) => <PriorityTag value={p} />,
                },
                {
                  title: 'Assignee',
                  dataIndex: 'assigneeId',
                  width: 200,
                  render: (a, r) => (
                    <Flex vertical gap={4}>
                      <Person id={a} />
                      <StationTag value={r.assigneeStation} />
                    </Flex>
                  ),
                },
                {
                  title: '',
                  key: 'arrow',
                  width: 30,
                  render: () => <ArrowRightOutlined style={{ opacity: 0.4 }} />,
                },
                {
                  title: 'Owning document',
                  dataIndex: 'docId',
                  width: 220,
                  render: (d, r) => (
                    <Flex vertical gap={4}>
                      <Link to={`/documents/${d}`}>
                        {d}
                        {r.docStale && (
                          <Tag color="error" style={{ marginInlineStart: 6 }}>
                            stale
                          </Tag>
                        )}
                      </Link>
                      <StationTag value={r.docStation} />
                    </Flex>
                  ),
                },
                {
                  title: 'Suggestion',
                  key: 'suggest',
                  width: 230,
                  fixed: screens.md ? 'right' : undefined,
                  render: (_x, r) =>
                    r.suggestedAssigneeId ? (
                      <Tooltip
                        title={
                          canAssign(r)
                            ? ''
                            : actor.role === 'admin'
                              ? 'Admins view audits read-only.'
                              : denyReason('assign', actor)
                        }
                      >
                        <Button
                          size="small"
                          icon={<SwapOutlined />}
                          disabled={!canAssign(r) || !can.actOnAudits(actor)}
                          loading={
                            reassign.isPending && reassign.variables?.ticketId === r.ticketId
                          }
                          onClick={() => reassign.mutate(r)}
                        >
                          Reassign to{' '}
                          {r.suggestedAssigneeId
                            ? r.suggestedAssigneeId && <Person id={r.suggestedAssigneeId} />
                            : ''}
                        </Button>
                      </Tooltip>
                    ) : (
                      <Typography.Text type="secondary">No owner on shift</Typography.Text>
                    ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="Owning station → assignee station">
            {v.flows.length ? (
              <Sankey
                data={v.flows}
                height={360}
                theme={mode === 'dark' ? 'classicDark' : 'classic'}
                layout={{ nodeAlign: 'justify', nodePadding: 0.08 }}
                scale={{
                  color: {
                    range: ['#C2410C', '#D46B08', '#1677FF', '#13A8A8', '#722ED1', '#5B8C00'],
                  },
                }}
                style={{
                  labelSpacing: 4,
                  labelFontWeight: 'bold',
                  nodeStrokeWidth: 1.2,
                  linkFillOpacity: 0.35,
                }}
              />
            ) : (
              <Typography.Text type="secondary">
                No mismatches — every open ticket sits with its owning station.
              </Typography.Text>
            )}
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
              Link width = number of open tickets. Computed from the ticket assignee’s station vs.
              the linked document’s station.
            </Typography.Paragraph>
          </Card>
        </Col>
      </Row>
    </>
  );
}
