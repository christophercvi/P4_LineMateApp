import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Card,
  Col,
  Descriptions,
  Drawer,
  Flex,
  Row,
  Segmented,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { CodeHighlighter, ThoughtChain } from '@ant-design/x';
import dayjs from 'dayjs';
import { api } from '@/api/client';
import type { NodeTrace } from '@/api/types';
import type { RunView } from '@/api/views';
import { can } from '@/auth/permissions';
import { useActor } from '@/auth/useAuth';
import { PageHeader } from '@/components/PageHeader';

const STATUS_COLOR: Record<RunView['status'], string> = {
  success: 'success',
  interrupted: 'warning',
  error: 'error',
  aborted: 'default',
};
const NODE_STATUS: Record<NodeTrace['status'], 'success' | 'error' | 'loading' | undefined> = {
  success: 'success',
  error: 'error',
  interrupted: undefined,
  skipped: undefined,
};

export default function AgentRuns() {
  const actor = useActor();
  const q = useQuery({ queryKey: ['runs'], queryFn: () => api<RunView[]>('/api/agent/runs') });
  const [open, setOpen] = useState<RunView | null>(null);
  const [nodeKey, setNodeKey] = useState<string | null>(null);
  const [graph, setGraph] = useState<'all' | 'ask' | 'triage'>('all');
  const runs = (q.data ?? [])
    .filter((r) => graph === 'all' || r.graph === graph)
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  const node = open?.nodes.find((n) => n.node === nodeKey) ?? open?.nodes[0];
  const avg = runs.length
    ? Math.round(runs.reduce((s, r) => s + r.durationMs, 0) / runs.length)
    : 0;

  return (
    <>
      <PageHeader
        title="Agent runs"
        subtitle={
          can.viewAllRuns(actor)
            ? 'All runs across users — traces from the LangGraph checkpointer'
            : 'Your own runs — Admins see everyone’s'
        }
        extra={
          <Segmented
            value={graph}
            onChange={(v) => setGraph(v as typeof graph)}
            options={[
              { label: 'All', value: 'all' },
              { label: 'Ask', value: 'ask' },
              { label: 'Triage', value: 'triage' },
            ]}
          />
        }
      />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Runs" value={runs.length} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Avg duration" value={(avg / 1000).toFixed(1)} suffix="s" />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic
              title="Interrupted"
              value={runs.filter((r) => r.status === 'interrupted').length}
              styles={{ content: { color: '#D48806' } }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Tokens out" value={runs.reduce((s, r) => s + r.tokensOut, 0)} />
          </Card>
        </Col>
      </Row>
      <Table<RunView>
        rowKey="id"
        size="small"
        loading={q.isLoading}
        dataSource={runs}
        onRow={(r) => ({
          onClick: () => {
            setOpen(r);
            setNodeKey(null);
          },
          style: { cursor: 'pointer' },
        })}
        columns={[
          {
            title: 'Run',
            dataIndex: 'id',
            render: (id: string) => <Typography.Link>{id}</Typography.Link>,
          },
          {
            title: 'Graph',
            dataIndex: 'graph',
            render: (g: string) => <Tag color={g === 'ask' ? 'blue' : 'purple'}>{g}</Tag>,
          },
          {
            title: 'Question / scope',
            dataIndex: 'question',
            ellipsis: true,
            render: (q: string | undefined, r) =>
              q ?? (r.graph === 'triage' ? 'Shift triage' : '—'),
          },
          { title: 'User', dataIndex: 'userName' },
          { title: 'Model', dataIndex: 'model', render: (m: string) => <Tag>{m}</Tag> },
          {
            title: 'Duration',
            dataIndex: 'durationMs',
            align: 'right',
            sorter: (a, b) => a.durationMs - b.durationMs,
            render: (d: number) => `${(d / 1000).toFixed(1)} s`,
          },
          {
            title: 'Tokens in/out',
            key: 'tok',
            align: 'right',
            render: (_: unknown, r) =>
              `${r.tokensIn.toLocaleString()} / ${r.tokensOut.toLocaleString()}`,
          },
          {
            title: 'Status',
            dataIndex: 'status',
            render: (s: RunView['status']) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          {
            title: 'Started',
            dataIndex: 'startedAt',
            render: (d: string) => dayjs(d).format('MMM D, HH:mm'),
          },
        ]}
      />
      <Drawer
        open={!!open}
        onClose={() => setOpen(null)}
        size="large"
        title={open ? `${open.id} · ${open.graph}` : ''}
        destroyOnHidden
      >
        {open && (
          <Flex vertical gap={16}>
            <Descriptions
              size="small"
              column={2}
              items={[
                { key: 'u', label: 'User', children: open.userName },
                { key: 'm', label: 'Model', children: open.model },
                {
                  key: 'd',
                  label: 'Duration',
                  children: `${(open.durationMs / 1000).toFixed(1)} s`,
                },
                {
                  key: 's',
                  label: 'Status',
                  children: <Tag color={STATUS_COLOR[open.status]}>{open.status}</Tag>,
                },
                ...(open.question
                  ? [{ key: 'q', label: 'Question', span: 2, children: open.question }]
                  : []),
              ]}
            />
            {open.status === 'interrupted' && (
              <Alert
                type="warning"
                showIcon
                title="Paused at human_approval"
                description="The checkpoint is saved. The run resumes from this node when the Kitchen Manager decides in Approvals."
              />
            )}
            <Row gutter={16}>
              <Col span={10}>
                <Typography.Text strong>Path taken</Typography.Text>
                <ThoughtChain
                  style={{ marginTop: 8 }}
                  items={open.nodes.map((n) => ({
                    key: n.node,
                    title: (
                      <Typography.Link
                        onClick={() => setNodeKey(n.node)}
                        strong={node?.node === n.node}
                      >
                        {n.node}
                      </Typography.Link>
                    ),
                    description:
                      n.status === 'interrupted' ? 'interrupted — waiting' : `${n.durationMs} ms`,
                    status: NODE_STATUS[n.status],
                  }))}
                />
              </Col>
              <Col span={14}>
                {node && (
                  <Flex vertical gap={8}>
                    <Typography.Text strong>
                      {node.node} <Tag>{node.status}</Tag>
                      <Tag>+{node.startedMs} ms</Tag>
                    </Typography.Text>
                    <CodeHighlighter lang="json" prismLightMode={false} header="input (state read)">
                      {JSON.stringify(node.input, null, 2)}
                    </CodeHighlighter>
                    <CodeHighlighter
                      lang="json"
                      prismLightMode={false}
                      header="output (state update)"
                    >
                      {JSON.stringify(node.output, null, 2)}
                    </CodeHighlighter>
                  </Flex>
                )}
              </Col>
            </Row>
          </Flex>
        )}
      </Drawer>
    </>
  );
}
