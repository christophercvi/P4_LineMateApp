import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Input,
  Popconfirm,
  Row,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  LockOutlined,
  PlayCircleOutlined,
  ReadOutlined,
  EditOutlined,
} from '@ant-design/icons';
import { CodeHighlighter } from '@ant-design/x';
import dayjs from 'dayjs';
import { api, ApiError } from '@/api/client';
import type { McpTool, ServiceToken } from '@/api/types';
import type { McpView } from '@/api/views';
import { can, ROLE_LABEL } from '@/auth/permissions';
import { useActor } from '@/auth/useAuth';
import { PageHeader } from '@/components/PageHeader';
import { IssueTokenButton } from '@/components/IssueTokenButton';
import { ListRow, RowList } from '@/components/ListRow';

interface TryResult {
  status: string;
  requiresApproval: boolean;
  message?: string;
  input: unknown;
  output?: unknown;
  latencyMs: number;
}

function ToolPanel({ tool }: { tool: McpTool }) {
  const actor = useActor();
  const allowed = can.callMcpTool(actor, tool.roles);
  const [input, setInput] = useState(JSON.stringify(tool.sampleInput, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: (body: unknown) =>
      api<TryResult>(`/api/mcp/tools/${tool.name}/try`, { body: { input: body } }),
  });
  const exec = () => {
    try {
      const parsed = JSON.parse(input);
      setParseError(null);
      run.mutate(parsed);
    } catch (e) {
      setParseError((e as Error).message);
    }
  };
  return (
    <Flex vertical gap={12}>
      <Descriptions
        size="small"
        column={{ xs: 1, sm: 2 }}
        items={[
          {
            key: 'a',
            label: 'Access',
            children:
              tool.access === 'read' ? (
                <Tag icon={<ReadOutlined />} color="blue">
                  read
                </Tag>
              ) : (
                <Tag icon={<EditOutlined />} color="orange">
                  write
                </Tag>
              ),
          },
          {
            key: 'h',
            label: 'Human approval',
            children: tool.requiresApproval ? (
              <Tag icon={<LockOutlined />} color="warning">
                required (interrupt)
              </Tag>
            ) : (
              'No'
            ),
          },
          {
            key: 'r',
            label: 'Roles',
            span: 'filled',
            children: tool.roles.map((r) => <Tag key={r}>{ROLE_LABEL[r]}</Tag>),
          },
          { key: 'c', label: 'Calls (7 d)', span: 'filled', children: tool.callsLast7d },
        ]}
      />
      <Typography.Paragraph style={{ margin: 0 }}>{tool.description}</Typography.Paragraph>
      <CodeHighlighter lang="json" prismLightMode={false} header="inputSchema">
        {JSON.stringify(tool.inputSchema, null, 2)}
      </CodeHighlighter>
      <Card
        size="small"
        title="Try it (dry run)"
        extra={
          <Tooltip
            title={
              allowed
                ? undefined
                : `Your role (${ROLE_LABEL[actor.role]}) is not in this tool's roles, so the MCP server would return 403.`
            }
          >
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={run.isPending}
              disabled={!allowed}
              onClick={exec}
            >
              Call tool
            </Button>
          </Tooltip>
        }
      >
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          autoSize={{ minRows: 3, maxRows: 10 }}
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          aria-label="Tool input JSON"
        />
        {parseError && (
          <Alert
            type="error"
            showIcon
            title="Invalid JSON"
            description={parseError}
            style={{ marginTop: 8 }}
          />
        )}
        {run.data && (
          <Flex vertical gap={8} style={{ marginTop: 12 }}>
            {run.data.requiresApproval ? (
              <Alert
                type="warning"
                showIcon
                title="Interrupted — approval required"
                description={run.data.message}
              />
            ) : (
              <Tag color="success" style={{ alignSelf: 'flex-start' }}>
                ok · {run.data.latencyMs} ms
              </Tag>
            )}
            {run.data.output !== undefined && (
              <CodeHighlighter lang="json" prismLightMode={false} header="result">
                {JSON.stringify(run.data.output, null, 2)}
              </CodeHighlighter>
            )}
          </Flex>
        )}
        {run.error && (
          <Alert
            type="error"
            showIcon
            title={(run.error as Error).message}
            style={{ marginTop: 8 }}
          />
        )}
      </Card>
    </Flex>
  );
}

export default function Mcp() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({ queryKey: ['mcp'], queryFn: () => api<McpView>('/api/mcp') });
  const [selected, setSelected] = useState<string>('search_documents');
  const revoke = useMutation({
    mutationFn: (id: string) =>
      api<ServiceToken>(`/api/mcp/tokens/${id}/revoke`, { method: 'POST' }),
    onSuccess: () => {
      message.success('Token revoked');
      qc.invalidateQueries({ queryKey: ['mcp'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });
  const tools = q.data?.tools ?? [];
  const tool = tools.find((t) => t.name === selected) ?? tools[0];

  const tabs = [
    {
      key: 'exposed',
      label: `Exposed tools (${tools.length})`,
      children: (
        <Row gutter={16}>
          <Col xs={24} md={8}>
            <Card
              size="small"
              title={
                <>
                  <ApiOutlined /> {q.data?.endpoint}
                </>
              }
            >
              <RowList
                items={tools}
                rowKey={(t) => t.name}
                render={(t) => (
                  <ListRow
                    onClick={() => setSelected(t.name)}
                    active={t.name === tool?.name}
                    title={<Typography.Text code>{t.name}</Typography.Text>}
                    description={
                      <Flex gap={4}>
                        {t.access === 'read' ? (
                          <Tag color="blue">read</Tag>
                        ) : (
                          <Tag color="orange">write</Tag>
                        )}
                        {t.requiresApproval && <Tag icon={<LockOutlined />}>approval</Tag>}
                      </Flex>
                    }
                  />
                )}
              />
            </Card>
          </Col>
          <Col xs={24} md={16}>
            {tool ? <ToolPanel key={tool.name} tool={tool} /> : <Empty />}
          </Col>
        </Row>
      ),
    },
    {
      key: 'consumed',
      label: `Consumed servers (${q.data?.servers.length ?? 0})`,
      children: (
        <Row gutter={[16, 16]}>
          {(q.data?.servers ?? []).map((s) => (
            <Col xs={24} lg={12} key={s.id}>
              <Card
                size="small"
                title={
                  <Flex gap={8} align="center">
                    <Badge
                      status={
                        s.status === 'connected'
                          ? 'success'
                          : s.status === 'degraded'
                            ? 'warning'
                            : 'error'
                      }
                    />
                    {s.name}
                  </Flex>
                }
                extra={<Tag>{s.transport}</Tag>}
              >
                <Descriptions
                  size="small"
                  column={{ xs: 1, sm: 2 }}
                  items={[
                    {
                      key: 'u',
                      label: 'URL',
                      span: 'filled',
                      children: (
                        <Typography.Text code copyable>
                          {s.url}
                        </Typography.Text>
                      ),
                    },
                    { key: 's', label: 'Status', children: s.status },
                    { key: 'l', label: 'Latency', children: `${s.latencyMs} ms` },
                    {
                      key: 'seen',
                      span: 'filled',
                      label: 'Last seen',
                      children: dayjs(s.lastSeen).format('MMM D, HH:mm'),
                    },
                  ]}
                />
                <Table
                  size="small"
                  rowKey="name"
                  pagination={false}
                  style={{ marginTop: 8 }}
                  dataSource={s.tools}
                  columns={[
                    {
                      title: 'Tool',
                      dataIndex: 'name',
                      render: (n: string) => <Typography.Text code>{n}</Typography.Text>,
                    },
                    { title: 'Description', dataIndex: 'description' },
                    {
                      title: 'Used by',
                      dataIndex: 'usedBy',
                      render: (u: string) => <Tag color="purple">{u}</Tag>,
                    },
                  ]}
                />
              </Card>
            </Col>
          ))}
        </Row>
      ),
    },
    {
      key: 'tokens',
      label: 'Service tokens',
      children: q.data?.tokens ? (
        <>
          <Flex justify="space-between" align="center" style={{ marginBottom: 12 }} gap={12} wrap>
            <Typography.Text type="secondary">
              MCP clients authenticate with a bearer service token. Tokens are stored as Argon2
              hashes.
            </Typography.Text>
            <IssueTokenButton />
          </Flex>
          <Table<ServiceToken>
            rowKey="id"
            size="small"
            scroll={{ x: 760 }}
            dataSource={q.data.tokens}
            columns={[
              {
                title: 'Name',
                dataIndex: 'name',
                render: (n: string) => <Typography.Text code>{n}</Typography.Text>,
              },
              { title: 'Client', dataIndex: 'client' },
              {
                title: 'Scopes',
                dataIndex: 'scopes',
                render: (s: string[]) =>
                  s.map((x) => (
                    <Tag key={x} color={x.endsWith('write') ? 'orange' : 'blue'}>
                      {x}
                    </Tag>
                  )),
              },
              {
                title: 'Last used',
                dataIndex: 'lastUsed',
                render: (d: string | null) => (d ? dayjs(d).format('MMM D, HH:mm') : 'never'),
              },
              {
                title: 'Expires',
                dataIndex: 'expiresAt',
                render: (d: string) => (
                  <span
                    style={{
                      color: dayjs(d).isBefore(dayjs().add(14, 'day')) ? '#D4380D' : undefined,
                    }}
                  >
                    {dayjs(d).format('MMM D, YYYY')}
                  </span>
                ),
              },
              {
                title: 'State',
                dataIndex: 'revoked',
                render: (r: boolean) =>
                  r ? <Tag>revoked</Tag> : <Tag color="success">active</Tag>,
              },
              {
                title: '',
                key: 'act',
                align: 'right',
                render: (_: unknown, t) =>
                  !t.revoked && (
                    <Popconfirm
                      title={`Revoke ${t.name}?`}
                      description="Clients using it get 401 on their next call."
                      okButtonProps={{ danger: true }}
                      onConfirm={() => revoke.mutate(t.id)}
                    >
                      <Button size="small" danger>
                        Revoke
                      </Button>
                    </Popconfirm>
                  ),
              },
            ]}
          />
        </>
      ) : (
        <Alert type="info" showIcon title="Only Admins manage service tokens." />
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="MCP console"
        subtitle="LineMate exposes its tools over MCP (streamable HTTP) and consumes two external MCP servers"
      />
      <Tabs items={tabs} />
    </>
  );
}
