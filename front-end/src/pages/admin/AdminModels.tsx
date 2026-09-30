import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Flex,
  Progress,
  Row,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { CloudDownloadOutlined, PoweroffOutlined, PushpinOutlined } from '@ant-design/icons';
import { api, ApiError } from '@/api/client';
import type { Capability, ModelAllowlist, ModelInfo, Role } from '@/api/types';
import type { AdminModelsView } from '@/api/views';
import { ROLE_LABEL } from '@/auth/permissions';
import { PageHeader } from '@/components/PageHeader';

const CAP_COLOR: Record<Capability, string> = {
  completion: 'default',
  tools: 'blue',
  thinking: 'purple',
  vision: 'cyan',
  embedding: 'green',
};
const CHAT_ROLES: Exclude<Role, 'service'>[] = [
  'line_cook',
  'sous_chef',
  'kitchen_manager',
  'admin',
];
const THINK_LABEL: Record<ModelInfo['thinking'], string> = {
  none: 'no',
  toggle: 'on / off',
  always: 'always',
};

export default function AdminModels() {
  const qc = useQueryClient();
  const { message } = App.useApp();
  const q = useQuery({
    queryKey: ['admin-models'],
    queryFn: () => api<AdminModelsView>('/api/admin/models'),
  });
  const [edited, setDraft] = useState<ModelAllowlist | null>(null);
  const draft = edited ?? q.data?.allowlist ?? null;

  const op = useMutation({
    mutationFn: ({ name, action }: { name: string; action: 'load' | 'unload' }) =>
      api<ModelInfo>(`/api/admin/models/${action}`, { body: { name } }),
    onSuccess: (m) => {
      message.success(`${m.name} ${m.loaded ? 'loaded' : 'unloaded'}`);
      qc.invalidateQueries({ queryKey: ['admin-models'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });
  const save = useMutation({
    mutationFn: (allowlist: ModelAllowlist) =>
      api<AdminModelsView>('/api/admin/models/allowlist', { method: 'PUT', body: { allowlist } }),
    onSuccess: (fresh) => {
      message.success('Allowlist saved');
      qc.setQueryData(['admin-models'], fresh);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ['models-mine'] });
    },
    onError: (e) => message.error(e instanceof ApiError ? e.message : 'Failed'),
  });

  const data = q.data;
  const limits = data?.limits;
  const budget = limits ? limits.hostMemoryGb - limits.reservedGb : 1;
  const chatModels = (data?.models ?? []).filter((m) => !m.capabilities.includes('embedding'));
  const dirty = !!draft && !!data && JSON.stringify(draft) !== JSON.stringify(data.allowlist);

  const toggle = (role: Exclude<Role, 'service'>, name: string, on: boolean) =>
    draft &&
    setDraft({
      ...draft,
      [role]: on ? [...draft[role], name] : draft[role].filter((n) => n !== name),
    });

  return (
    <>
      <PageHeader
        title="Model registry"
        subtitle="Ollama models, their capabilities (from /api/show), memory residency and which roles may use each one"
      />
      {limits && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col xs={24} md={10}>
            <Card size="small" title="Memory in use">
              <Progress
                percent={Math.round((limits.loadedGb / budget) * 100)}
                format={() => `${limits.loadedGb} / ${budget} GB`}
                status={limits.loadedGb / budget > 0.85 ? 'exception' : 'normal'}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {limits.hostMemoryGb} GB host, {limits.reservedGb} GB reserved for FastAPI, Chroma
                and the OS
              </Typography.Text>
            </Card>
          </Col>
          <Col xs={12} md={7}>
            <Card size="small">
              <Statistic
                title="Loaded models"
                value={limits.loadedCount}
                suffix={`/ ${limits.maxLoadedModels || 'auto'}`}
              />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                OLLAMA_MAX_LOADED_MODELS{limits.maxLoadedModels ? '' : ' · Ollama default'}
              </Typography.Text>
            </Card>
          </Col>
          <Col xs={12} md={7}>
            <Card size="small">
              <Statistic title="Parallel requests / model" value={limits.numParallel || 'auto'} />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                OLLAMA_NUM_PARALLEL{limits.numParallel ? '' : ' · Ollama default'}
              </Typography.Text>
            </Card>
          </Col>
        </Row>
      )}
      <Card size="small" title="Installed models" style={{ marginBottom: 16 }}>
        <Table<ModelInfo>
          rowKey="name"
          size="small"
          loading={q.isLoading}
          dataSource={data?.models}
          pagination={false}
          scroll={{ x: 900 }}
          columns={[
            {
              title: 'Model',
              dataIndex: 'name',
              render: (n: string, m) => (
                <Flex vertical>
                  <Typography.Text code>{n}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {m.purpose}
                  </Typography.Text>
                </Flex>
              ),
            },
            { title: 'Params', dataIndex: 'parameters', width: 80 },
            { title: 'Quant', dataIndex: 'quantization', width: 80 },
            {
              title: 'Size',
              dataIndex: 'sizeGb',
              width: 80,
              align: 'right',
              render: (s: number) => `${s} GB`,
            },
            {
              title: 'Context',
              dataIndex: 'contextLength',
              width: 90,
              align: 'right',
              render: (c: number) => `${Math.round(c / 1024)}K`,
            },
            {
              title: 'Capabilities',
              dataIndex: 'capabilities',
              render: (caps: Capability[]) => (
                <Flex gap={4} wrap>
                  {caps.map((c) => (
                    <Tag key={c} color={CAP_COLOR[c]}>
                      {c}
                    </Tag>
                  ))}
                </Flex>
              ),
            },
            {
              title: 'Reasoning',
              dataIndex: 'thinking',
              width: 100,
              render: (t: ModelInfo['thinking']) => (
                <Tag color={t === 'none' ? 'default' : 'purple'}>{THINK_LABEL[t]}</Tag>
              ),
            },
            {
              title: 'keep_alive',
              dataIndex: 'keepAlive',
              width: 110,
              render: (k: string) =>
                k.startsWith('-1') ? (
                  <Tag icon={<PushpinOutlined />} color="green">
                    pinned
                  </Tag>
                ) : (
                  <Tag>{k}</Tag>
                ),
            },
            {
              title: 'State',
              key: 'state',
              width: 150,
              align: 'right',
              render: (_: unknown, m) => (
                <Flex gap={6} justify="flex-end" align="center">
                  {m.loaded ? <Tag color="success">loaded</Tag> : <Tag>unloaded</Tag>}
                  {m.loaded ? (
                    <Tooltip
                      title={
                        m.keepAlive.startsWith('-1')
                          ? 'Pinned with keep_alive -1'
                          : 'Unload (keep_alive 0)'
                      }
                    >
                      <Button
                        size="small"
                        icon={<PoweroffOutlined />}
                        disabled={m.keepAlive.startsWith('-1')}
                        onClick={() => op.mutate({ name: m.name, action: 'unload' })}
                        aria-label={`Unload ${m.name}`}
                      />
                    </Tooltip>
                  ) : (
                    <Tooltip title="Load now (evicts the largest unpinned model if the limit is reached)">
                      <Button
                        size="small"
                        icon={<CloudDownloadOutlined />}
                        onClick={() => op.mutate({ name: m.name, action: 'load' })}
                        aria-label={`Load ${m.name}`}
                      />
                    </Tooltip>
                  )}
                </Flex>
              ),
            },
          ]}
        />
      </Card>
      <Card
        size="small"
        title="Per-role allowlist"
        extra={
          <Flex gap={8}>
            <Button disabled={!dirty} onClick={() => setDraft(data?.allowlist ?? null)}>
              Discard
            </Button>
            <Button
              type="primary"
              disabled={!dirty}
              loading={save.isPending}
              onClick={() => draft && save.mutate(draft)}
            >
              Save allowlist
            </Button>
          </Flex>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          title="Embedding models are not listed: they cannot answer chat. The Ask page only offers models on the signed-in role’s list."
        />
        <Table
          rowKey="name"
          size="small"
          pagination={false}
          dataSource={chatModels}
          columns={[
            {
              title: 'Model',
              dataIndex: 'name',
              render: (n: string, m: ModelInfo) => (
                <Flex gap={6} align="center">
                  <Typography.Text code>{n}</Typography.Text>
                  {m.capabilities.includes('vision') && <Tag color="cyan">vision</Tag>}
                  {m.thinking !== 'none' && <Tag color="purple">thinking</Tag>}
                </Flex>
              ),
            },
            ...CHAT_ROLES.map((role) => ({
              title: ROLE_LABEL[role],
              key: role,
              align: 'center' as const,
              render: (_: unknown, m: ModelInfo) => (
                <Checkbox
                  checked={draft?.[role].includes(m.name) ?? false}
                  onChange={(e) => toggle(role, m.name, e.target.checked)}
                  aria-label={`${m.name} for ${ROLE_LABEL[role]}`}
                />
              ),
            })),
          ]}
        />
      </Card>
    </>
  );
}
