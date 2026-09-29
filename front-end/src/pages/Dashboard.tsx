import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Flex,
  Row,
  Skeleton,
  Statistic,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { Tiny } from '@ant-design/charts';
import {
  ArrowRightOutlined,
  CheckSquareOutlined,
  FileSearchOutlined,
  MessageOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { api } from '@/api/client';
import type { ActivityEvent, StaleRow, Ticket } from '@/api/types';
import { useActor } from '@/auth/useAuth';
import { useSession, useUi } from '@/auth/store';
import { can, ROLE_LABEL } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { ListRow, RowList } from '@/components/ListRow';
import { Person, PriorityTag, StatusTag } from '@/components/tags';
import { EMBER, PRIORITY } from '@/theme/tokens';

dayjs.extend(relativeTime);

interface DashboardData {
  scope: string;
  kpis: {
    open: number;
    critical: number;
    high: number;
    staleDocs: number;
    pendingApprovals: number;
    myOpen: number;
    resolutionRate: number;
  };
  openTrend: number[];
  closedTrend: number[];
  myTickets: (Ticket & { commentCount: number; docStale: boolean })[];
  staleCited: StaleRow[];
  triageTeaser: { ticketId: string; score: number; reasons: string[]; ticket: Ticket }[];
  activity: (ActivityEvent & { actorName: string })[];
  admin: { runsToday: number; modelsLoaded: number; vectors: number; errors: number } | null;
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

const activityColor = (e: ActivityEvent) =>
  e.action.includes('approved')
    ? 'green'
    : e.action.includes('rejected')
      ? 'red'
      : e.targetType === 'document'
        ? 'blue'
        : e.targetType === 'approval'
          ? 'orange'
          : 'gray';

export default function Dashboard() {
  const actor = useActor();
  const name = useSession((s) => s.claims?.name ?? '');
  const mode = useUi((s) => s.mode);
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api<DashboardData>('/api/dashboard'),
  });

  if (isLoading || !data) return <Skeleton active paragraph={{ rows: 12 }} />;
  const k = data.kpis;
  const chartTheme = mode === 'dark' ? 'classicDark' : 'classic';
  const track = mode === 'dark' ? '#3A3430' : '#EEE9E2';
  // Tiny charts in @ant-design/plots 2.x need object rows plus xField/yField (plain number[] has no encode)
  const openSeries = data.openTrend.map((value, day) => ({ day, value }));
  const closedSeries = data.closedTrend.map((value, day) => ({ day, value }));

  const kpiCards = [
    {
      key: 'open',
      title: `Open tickets · ${data.scope}`,
      value: k.open,
      suffix: (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          last 14 days
        </Typography.Text>
      ),
      chart: (
        <Tiny.Line
          data={openSeries}
          xField="day"
          yField="value"
          shapeField="smooth"
          height={48}
          theme={chartTheme}
          style={{ stroke: EMBER, lineWidth: 2 }}
        />
      ),
      onClick: () => navigate('/tickets'),
    },
    {
      key: 'crit',
      title: 'Critical / High open',
      value: `${k.critical} / ${k.high}`,
      valueColor: PRIORITY.critical.hex,
      suffix: (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          resolved per week · 12 wks
        </Typography.Text>
      ),
      chart: (
        <Tiny.Column
          data={closedSeries}
          xField="day"
          yField="value"
          height={48}
          theme={chartTheme}
          style={{ fill: '#5B8C00' }}
        />
      ),
      onClick: () => navigate('/tickets?priority=critical,high'),
    },
    {
      key: 'rate',
      title: 'Resolution rate · 30 days',
      value: `${Math.round(k.resolutionRate * 100)}%`,
      suffix: (
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          resolved ÷ opened
        </Typography.Text>
      ),
      chart: (
        <Tiny.Ring
          percent={k.resolutionRate}
          height={56}
          width={56}
          color={[track, '#5B8C00']}
          theme={chartTheme}
        />
      ),
      inlineChart: true,
    },
    can.viewApprovals(actor)
      ? {
          key: 'appr',
          title: 'Pending approvals',
          value: k.pendingApprovals,
          valueColor: k.pendingApprovals ? '#D46B08' : undefined,
          suffix: (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {can.approve(actor) ? 'waiting for you' : 'waiting for the Kitchen Manager'}
            </Typography.Text>
          ),
          chart: (
            <Tiny.Progress
              percent={Math.min(1, k.pendingApprovals / 5)}
              height={20}
              color={[track, '#D46B08']}
              theme={chartTheme}
            />
          ),
          onClick: () => navigate('/approvals'),
        }
      : {
          key: 'mine',
          title: 'Assigned to me',
          value: k.myOpen,
          suffix: (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              open
            </Typography.Text>
          ),
          chart: (
            <Tiny.Progress
              percent={Math.min(1, k.myOpen / 6)}
              height={20}
              color={[track, EMBER]}
              theme={chartTheme}
            />
          ),
          onClick: () => navigate('/tickets?mine=true'),
        },
  ];

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${name.split(' ')[0]}`}
        subtitle={`${ROLE_LABEL[actor.role]} · ${data.scope} · ${dayjs().format('dddd D MMMM')}`}
        extra={
          <>
            {can.ask(actor) && (
              <Button icon={<MessageOutlined />} onClick={() => navigate('/ask')}>
                Ask LineMate
              </Button>
            )}
            {can.createTicket(actor) && (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => navigate('/tickets?new=1')}
              >
                New ticket
              </Button>
            )}
          </>
        }
      />

      {data.staleCited.length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          style={{ marginBottom: 16 }}
          title={`${data.staleCited.length} stale SOP${data.staleCited.length > 1 ? 's were' : ' was'} cited in answers this week`}
          description={
            <Flex gap={8} wrap>
              {data.staleCited.map((s) => (
                <Tag
                  key={s.docId}
                  style={{ cursor: 'pointer', whiteSpace: 'normal', maxWidth: '100%' }}
                  onClick={() => navigate(`/documents/${s.docId}`)}
                >
                  {s.docId} · {s.title} · {s.daysSinceReview} d · cited {s.citedLast7d}×
                </Tag>
              ))}
            </Flex>
          }
          action={
            can.viewAudits(actor) ? (
              <Button size="small" onClick={() => navigate('/audits/stale')}>
                Open audit
              </Button>
            ) : undefined
          }
        />
      )}

      <Row gutter={[16, 16]}>
        {kpiCards.map((c) => (
          <Col key={c.key} xs={24} sm={12} xl={6}>
            <Card hoverable={!!c.onClick} onClick={c.onClick} style={{ height: '100%' }}>
              <Flex justify="space-between" align="center" gap={8}>
                <Statistic
                  title={c.title}
                  value={c.value}
                  styles={{ content: { color: c.valueColor, fontVariantNumeric: 'tabular-nums' } }}
                />
                {c.inlineChart && c.chart}
              </Flex>
              {!c.inlineChart && <div style={{ marginTop: 8 }}>{c.chart}</div>}
              <div style={{ marginTop: 4 }}>{c.suffix}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {data.admin && (
        <Card
          title="System today"
          style={{ marginTop: 16 }}
          extra={
            <Button type="link" onClick={() => navigate('/agent/runs')}>
              Agent runs <ArrowRightOutlined />
            </Button>
          }
        >
          <Row gutter={16}>
            <Col xs={12} md={6}>
              <Statistic title="Agent runs" value={data.admin.runsToday} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Models loaded" value={data.admin.modelsLoaded} suffix="/ 4" />
            </Col>
            <Col xs={12} md={6}>
              <Statistic title="Vectors indexed" value={data.admin.vectors} />
            </Col>
            <Col xs={12} md={6}>
              <Statistic
                title="Failed runs"
                value={data.admin.errors}
                styles={{
                  content: { color: data.admin.errors ? PRIORITY.critical.hex : undefined },
                }}
              />
            </Col>
          </Row>
        </Card>
      )}

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={15}>
          <Flex vertical gap={16}>
            <Card
              title={
                <Flex gap={8} align="center">
                  <ThunderboltOutlined style={{ color: EMBER }} />
                  Handle first
                </Flex>
              }
              extra={
                can.runTriage(actor) ? (
                  <Button type="link" onClick={() => navigate('/triage')}>
                    Run shift triage <ArrowRightOutlined />
                  </Button>
                ) : null
              }
            >
              <RowList
                items={data.triageTeaser}
                rowKey={(r) => r.ticketId}
                empty={<Empty description="Nothing urgent — nice." />}
                render={(r, i) => (
                  <ListRow
                    onClick={() => navigate(`/tickets/${r.ticketId}`)}
                    extra={<Person id={r.ticket.assigneeId} />}
                    avatar={
                      <Typography.Title level={4} style={{ margin: 0, width: 24, color: EMBER }}>
                        {i + 1}
                      </Typography.Title>
                    }
                    title={
                      <Flex gap={8} wrap align="center">
                        <Typography.Text strong>{r.ticketId}</Typography.Text>
                        <PriorityTag value={r.ticket.priority} />
                        <span>{r.ticket.title}</span>
                      </Flex>
                    }
                    description={r.reasons.join(' · ')}
                  />
                )}
              />
            </Card>
            <Card
              title="My tickets"
              extra={
                <Button type="link" onClick={() => navigate('/tickets?mine=true')}>
                  All <ArrowRightOutlined />
                </Button>
              }
            >
              <RowList
                items={data.myTickets}
                rowKey={(t) => t.id}
                empty={
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      actor.crewMemberId
                        ? 'Nothing assigned to you'
                        : 'Admins are not assigned kitchen tickets'
                    }
                  />
                }
                render={(t) => (
                  <ListRow
                    onClick={() => navigate(`/tickets/${t.id}`)}
                    extra={
                      <Typography.Text type="secondary">
                        {dayjs(t.updatedAt).fromNow()}
                      </Typography.Text>
                    }
                    title={
                      <Flex gap={8} wrap align="center">
                        <Typography.Text strong>{t.id}</Typography.Text>
                        <PriorityTag value={t.priority} />
                        <StatusTag value={t.status} />
                        {t.title}
                      </Flex>
                    }
                    description={`${t.commentCount} comment${t.commentCount === 1 ? '' : 's'}${t.docStale ? ' · linked SOP is stale' : ''}`}
                  />
                )}
              />
            </Card>
          </Flex>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="Kitchen activity" style={{ height: '100%' }}>
            <Timeline
              items={data.activity.map((e) => ({
                color: activityColor(e),
                title: (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {dayjs(e.at).fromNow()}
                  </Typography.Text>
                ),
                content: (
                  <div>
                    <Typography.Text strong>{e.actorName}</Typography.Text> {e.action}{' '}
                    <Typography.Link
                      onClick={() =>
                        navigate(
                          e.targetType === 'ticket'
                            ? `/tickets/${e.target}`
                            : e.targetType === 'document'
                              ? `/documents/${e.target}`
                              : '/approvals',
                        )
                      }
                    >
                      {e.target}
                    </Typography.Link>
                    {e.detail && (
                      <Typography.Paragraph
                        type="secondary"
                        ellipsis={{ rows: 2 }}
                        style={{ margin: 0, fontSize: 13 }}
                      >
                        {e.detail}
                      </Typography.Paragraph>
                    )}
                  </div>
                ),
              }))}
            />
            <Flex gap={8} wrap>
              <Button
                size="small"
                icon={<FileSearchOutlined />}
                onClick={() => navigate('/documents')}
              >
                Browse documents
              </Button>
              {can.viewApprovals(actor) && (
                <Button
                  size="small"
                  icon={<CheckSquareOutlined />}
                  onClick={() => navigate('/approvals')}
                >
                  Approvals
                </Button>
              )}
            </Flex>
          </Card>
        </Col>
      </Row>
    </>
  );
}
