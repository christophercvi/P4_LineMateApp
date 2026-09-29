import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Badge,
  Button,
  Card,
  Empty,
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
  AppstoreOutlined,
  CommentOutlined,
  PaperClipOutlined,
  PlusOutlined,
  SwapOutlined,
  UnorderedListOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { api } from '@/api/client';
import type { Priority, TicketStatus } from '@/api/types';
import type { TicketListItem } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { can } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';
import { Person, PriorityTag, StationTag, StatusTag } from '@/components/tags';
import { TicketFormDrawer } from '@/components/TicketFormDrawer';
import { PRIORITIES, PRIORITY, STATUS, STATUSES } from '@/theme/tokens';
import { crew, stations } from '@/api/lookups';

dayjs.extend(relativeTime);

export default function TicketList() {
  const actor = useActor();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<'table' | 'board'>('table');
  const [q, setQ] = useState('');
  const [status, setStatus] = useState<TicketStatus[]>(['open', 'in_progress', 'blocked']);
  const [priority, setPriority] = useState<Priority[]>(
    () => (params.get('priority')?.split(',').filter(Boolean) as Priority[]) ?? [],
  );
  const [station, setStation] = useState<string | undefined>();
  const [assignee, setAssignee] = useState<string | undefined>();
  const [mine, setMine] = useState(params.get('mine') === 'true');
  const creating = params.get('new') === '1';

  const tickets = useQuery({
    queryKey: [
      'tickets',
      { q, status: view === 'board' ? [] : status, priority, station, assignee, mine },
    ],
    queryFn: () =>
      api<TicketListItem[]>('/api/tickets', {
        query: {
          q,
          status: view === 'board' ? undefined : status,
          priority,
          station,
          assignee,
          mine: mine || undefined,
        },
      }),
  });

  const data = useMemo(
    () =>
      [...(tickets.data ?? [])].sort(
        (a, b) =>
          PRIORITY[b.priority].rank - PRIORITY[a.priority].rank ||
          b.updatedAt.localeCompare(a.updatedAt),
      ),
    [tickets.data],
  );

  const columns: TableColumnsType<TicketListItem> = [
    {
      title: 'Ticket',
      dataIndex: 'title',
      key: 'title',
      width: 380,
      render: (_v, t) => (
        <Flex vertical style={{ minWidth: 0 }}>
          <Link to={`/tickets/${t.id}`}>
            <Typography.Text strong ellipsis style={{ maxWidth: 340 }}>
              {t.title}
            </Typography.Text>
          </Link>
          <Flex gap={8} align="center" wrap>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t.id}
            </Typography.Text>
            {t.commentCount > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <CommentOutlined /> {t.commentCount}
              </Typography.Text>
            )}
            {t.attachmentCount > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <PaperClipOutlined /> {t.attachmentCount}
              </Typography.Text>
            )}
            {t.docStale && (
              <Tooltip title="Linked SOP is past its 180-day review">
                <Tag color="error" icon={<WarningOutlined />} style={{ fontSize: 11 }}>
                  Stale SOP
                </Tag>
              </Tooltip>
            )}
            {t.mismatch && (
              <Tooltip title="Assignee works outside the station that owns the linked document">
                <Tag color="warning" icon={<SwapOutlined />} style={{ fontSize: 11 }}>
                  Ownership
                </Tag>
              </Tooltip>
            )}
          </Flex>
        </Flex>
      ),
    },
    {
      title: 'Priority',
      dataIndex: 'priority',
      key: 'priority',
      width: 110,
      sorter: (a, b) => PRIORITY[a.priority].rank - PRIORITY[b.priority].rank,
      render: (v) => <PriorityTag value={v} />,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (v) => <StatusTag value={v} />,
    },
    {
      title: 'Station',
      dataIndex: 'station',
      key: 'station',
      width: 150,
      render: (v) => <StationTag value={v} />,
    },
    {
      title: 'Assignee',
      dataIndex: 'assigneeId',
      key: 'assignee',
      width: 180,
      render: (v) => <Person id={v} />,
    },
    {
      title: 'Related doc',
      dataIndex: 'relatedDocId',
      key: 'doc',
      width: 130,
      render: (v) =>
        v ? (
          <Link to={`/documents/${v}`}>{v}</Link>
        ) : (
          <Typography.Text type="secondary">—</Typography.Text>
        ),
    },
    {
      title: 'Updated',
      dataIndex: 'updatedAt',
      key: 'updated',
      width: 130,
      sorter: (a, b) => a.updatedAt.localeCompare(b.updatedAt),
      render: (v) => (
        <Tooltip title={dayjs(v).format('D MMM YYYY HH:mm')}>{dayjs(v).fromNow()}</Tooltip>
      ),
    },
  ];

  const board = (
    <Flex gap={12} style={{ overflowX: 'auto', paddingBottom: 8 }} align="flex-start">
      {STATUSES.map((s) => {
        const col = data.filter((t) => t.status === s);
        return (
          <Card
            key={s}
            size="small"
            className="lm-kanban-col"
            title={
              <Flex gap={8} align="center">
                <Badge color={STATUS[s].color === 'default' ? '#8c8c8c' : STATUS[s].color} />
                {STATUS[s].label}
                <Tag variant="filled">{col.length}</Tag>
              </Flex>
            }
            styles={{ body: { padding: 8, maxHeight: 640, overflowY: 'auto' } }}
          >
            <Flex vertical gap={8}>
              {col.length === 0 && (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Nothing here" />
              )}
              {col.map((t) => (
                <Card
                  key={t.id}
                  size="small"
                  hoverable
                  onClick={() => navigate(`/tickets/${t.id}`)}
                  style={{ borderInlineStart: `3px solid ${PRIORITY[t.priority].hex}` }}
                >
                  <Flex vertical gap={6}>
                    <Flex justify="space-between" align="center">
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {t.id}
                      </Typography.Text>
                      <PriorityTag value={t.priority} />
                    </Flex>
                    <Typography.Text strong>{t.title}</Typography.Text>
                    <Flex justify="space-between" align="center">
                      <Person id={t.assigneeId} />
                      <Space size={8}>
                        {t.docStale && <WarningOutlined style={{ color: PRIORITY.critical.hex }} />}
                        {t.mismatch && <SwapOutlined style={{ color: PRIORITY.high.hex }} />}
                        {t.commentCount > 0 && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            <CommentOutlined /> {t.commentCount}
                          </Typography.Text>
                        )}
                      </Space>
                    </Flex>
                  </Flex>
                </Card>
              ))}
            </Flex>
          </Card>
        );
      })}
    </Flex>
  );

  const counts = PRIORITIES.map((p) => ({
    p,
    n: data.filter((t) => t.priority === p && ['open', 'in_progress', 'blocked'].includes(t.status))
      .length,
  }));

  return (
    <>
      <PageHeader
        title="Tickets"
        subtitle={
          <Flex gap={8} wrap align="center">
            {counts.map(({ p, n }) => (
              <Tag key={p} color={PRIORITY[p].color}>
                {n} {PRIORITY[p].label}
              </Tag>
            ))}
            <span>open in this view</span>
          </Flex>
        }
        extra={
          <>
            <Segmented
              value={view}
              onChange={(v) => setView(v as typeof view)}
              options={[
                { value: 'table', icon: <UnorderedListOutlined />, label: 'Table' },
                { value: 'board', icon: <AppstoreOutlined />, label: 'Board' },
              ]}
            />
            {can.createTicket(actor) ? (
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setParams({ new: '1' })}
              >
                New ticket
              </Button>
            ) : (
              <Tooltip title="Admins run the system and do not create kitchen tickets.">
                <Button type="primary" icon={<PlusOutlined />} disabled>
                  New ticket
                </Button>
              </Tooltip>
            )}
          </>
        }
      />
      <Card styles={{ body: { padding: view === 'table' ? 0 : 16 } }}>
        <Flex gap={12} wrap align="center" style={{ padding: view === 'table' ? 16 : '0 0 16px' }}>
          <Input.Search
            placeholder="Search tickets…"
            allowClear
            style={{ width: 240 }}
            onSearch={setQ}
            onChange={(e) => !e.target.value && setQ('')}
          />
          {view === 'table' && (
            <Select
              mode="multiple"
              allowClear
              placeholder="Status"
              style={{ minWidth: 220 }}
              value={status}
              onChange={setStatus}
              options={STATUSES.map((s) => ({ value: s, label: STATUS[s].label }))}
              maxTagCount="responsive"
            />
          )}
          <Select
            mode="multiple"
            allowClear
            placeholder="Priority"
            style={{ minWidth: 180 }}
            value={priority}
            onChange={setPriority}
            options={PRIORITIES.map((p) => ({ value: p, label: PRIORITY[p].label }))}
            maxTagCount="responsive"
          />
          <Select
            allowClear
            placeholder="Station"
            style={{ width: 160 }}
            value={station}
            onChange={setStation}
            options={stations().map((s) => ({ value: s.id, label: s.name }))}
          />
          <Select
            allowClear
            placeholder="Assignee"
            style={{ width: 190 }}
            value={assignee}
            onChange={setAssignee}
            showSearch={{ optionFilterProp: 'label' }}
            options={crew().map((c) => ({ value: c.id, label: c.name }))}
          />
          {actor.crewMemberId && (
            <Space>
              <Switch size="small" checked={mine} onChange={setMine} aria-label="Assigned to me" />
              <Typography.Text>Assigned to me</Typography.Text>
            </Space>
          )}
        </Flex>
        {view === 'table' ? (
          <Table<TicketListItem>
            rowKey="id"
            size="middle"
            loading={tickets.isLoading}
            columns={columns}
            dataSource={data}
            scroll={{ x: 1200 }}
            onRow={(t) => ({ onDoubleClick: () => navigate(`/tickets/${t.id}`) })}
            pagination={{ pageSize: 12, showSizeChanger: false, showTotal: (t) => `${t} tickets` }}
          />
        ) : (
          board
        )}
      </Card>
      <TicketFormDrawer open={creating} onClose={() => setParams({})} />
    </>
  );
}
