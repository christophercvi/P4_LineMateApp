import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Flex,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd';
import { PauseCircleOutlined, ThunderboltOutlined, WarningOutlined } from '@ant-design/icons';
import { ThoughtChain } from '@ant-design/x';
import { XStream } from '@ant-design/x-sdk';
import { api, apiFetch } from '@/api/client';
import type { ChatInterrupt, TriageNodeEvent, TriageResult, TriageRiskKind } from '@/api/types';
import type { MyModelsView, TicketListItem } from '@/api/views';
import { useActor } from '@/auth/useAuth';
import { ApprovalSurface } from '@/chat/ApprovalCard';
import { Md } from '@/components/Md';
import { PageHeader } from '@/components/PageHeader';
import { ListRow, RowList } from '@/components/ListRow';
import { PriorityTag, StationTag, StatusTag } from '@/components/tags';

const PLANNED: { node: string; title: string }[] = [
  { node: 'load_tickets', title: 'Load open tickets' },
  { node: 'rank', title: 'Rank by urgency' },
  { node: 'enrich_with_docs', title: 'Enrich with documents & roster' },
  { node: 'check_supplies', title: 'Check supplier stock (MCP)' },
  { node: 'detect_risks', title: 'Detect risks' },
  { node: 'propose_actions', title: 'Propose actions' },
  { node: 'summarize', title: 'Write the shift summary' },
  { node: 'human_approval', title: 'Wait for approval (interrupt)' },
];

const RISK_TAG: Record<TriageRiskKind, { color: string; label: string }> = {
  combined: { color: 'red', label: 'Combined risk' },
  ownership_mismatch: { color: 'orange', label: 'Ownership mismatch' },
  stale_sop: { color: 'gold', label: 'Stale SOP' },
  shortage: { color: 'volcano', label: 'Shortage' },
  staffing: { color: 'purple', label: 'Staffing gap' },
};
/** Unknown kinds from a newer back-end still render instead of breaking the page. */
const riskTag = (kind: string) =>
  RISK_TAG[kind as TriageRiskKind] ?? { color: 'default', label: kind.replace(/_/g, ' ') };

export default function Triage() {
  const actor = useActor();
  const qc = useQueryClient();
  const mine = useQuery({
    queryKey: ['models-mine'],
    queryFn: () => api<MyModelsView>('/api/models'),
  });
  const tickets = useQuery({
    queryKey: ['tickets', 'all-open'],
    queryFn: () =>
      api<TicketListItem[]>('/api/tickets', { query: { status: 'open,in_progress,blocked' } }),
  });
  const toolModels = (mine.data?.models ?? []).filter(
    (m) => m.capabilities.includes('completion') && m.capabilities.includes('tools'),
  );
  const [model, setModel] = useState<string>();
  const current =
    model ?? toolModels.find((m) => m.name.startsWith('qwen3'))?.name ?? toolModels[0]?.name;
  const [nodes, setNodes] = useState<TriageNodeEvent[]>([]);
  const [summary, setSummary] = useState('');
  const [result, setResult] = useState<TriageResult | null>(null);
  const [interrupts, setInterrupts] = useState<ChatInterrupt[]>([]);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const run = async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setNodes([]);
    setSummary('');
    setResult(null);
    setInterrupts([]);
    setElapsed(null);
    setError(null);
    setRunning(true);
    try {
      const res = await apiFetch('/api/agent/triage/stream', {
        method: 'POST',
        body: JSON.stringify({ model: current }),
        signal: ac.signal,
      });
      if (!res.ok || !res.body)
        throw new Error(
          ((await res.json().catch(() => ({}))) as { detail?: string }).detail ??
            `HTTP ${res.status}`,
        );
      for await (const chunk of XStream({ readableStream: res.body })) {
        const event = String(chunk.event ?? '').trim();
        const data = chunk.data ? JSON.parse(chunk.data) : null;
        if (event === 'node') {
          const n = data as TriageNodeEvent;
          setNodes((prev) => {
            const i = prev.findIndex((p) => p.node === n.node);
            if (i < 0) return [...prev, n];
            const next = [...prev];
            next[i] = { ...next[i], ...n };
            return next;
          });
        } else if (event === 'content') setSummary((s) => s + String(data ?? ''));
        else if (event === 'result') setResult(data as TriageResult);
        else if (event === 'interrupt') setInterrupts((prev) => [...prev, data as ChatInterrupt]);
        else if (event === 'error')
          setError(
            (data as { message?: string } | null)?.message ??
              'The triage agent stopped with an error.',
          );
        else if (event === 'done') setElapsed((data as { elapsed_ms: number }).elapsed_ms);
      }
      qc.invalidateQueries({ queryKey: ['approvals'] });
      qc.invalidateQueries({ queryKey: ['runs'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const stop = () => {
    abortRef.current?.abort();
    setNodes((prev) =>
      prev.map((n) =>
        n.status === 'loading' ? { ...n, status: 'error', description: 'Stopped' } : n,
      ),
    );
  };

  const byId = new Map((tickets.data ?? []).map((t) => [t.id, t]));
  const started = nodes.length > 0;
  const chainItems = PLANNED.filter(
    (p) => p.node !== 'human_approval' || nodes.some((n) => n.node === 'human_approval'),
  ).map((p) => {
    const n = nodes.find((x) => x.node === p.node);
    const waiting = n?.node === 'human_approval' && n.status === 'loading';
    return {
      key: p.node,
      title: (
        <Flex gap={8} align="center">
          <Typography.Text code style={{ fontSize: 12 }}>
            {p.node}
          </Typography.Text>
          <span>{n?.title ?? p.title}</span>
        </Flex>
      ),
      description: waiting
        ? 'Paused — the graph resumes when the Kitchen Manager decides'
        : n?.description
          ? `${n.description}${n.durationMs ? ` · ${(n.durationMs / 1000).toFixed(1)} s` : ''}`
          : undefined,
      status: waiting ? undefined : n ? (n.status as 'loading' | 'success' | 'error') : undefined,
      icon: waiting ? <PauseCircleOutlined style={{ color: '#FAAD14' }} /> : undefined,
      blink: n?.status === 'loading' && !waiting,
    };
  });

  return (
    <>
      <PageHeader
        title="Shift triage"
        subtitle={`LangGraph agent · ${actor.role === 'sous_chef' ? 'your station only' : 'all stations'} · ranks open tickets, checks linked SOPs and assignees, then proposes actions for approval`}
        extra={
          <Space>
            <Select
              value={current}
              onChange={setModel}
              style={{ width: 200 }}
              loading={mine.isLoading}
              placeholder={mine.data && !toolModels.length ? 'No tool-capable models' : 'Model'}
              options={toolModels.map((m) => ({ value: m.name, label: m.name }))}
              aria-label="Agent model"
            />
            {running ? (
              <Button danger onClick={stop}>
                Stop
              </Button>
            ) : (
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                onClick={run}
                disabled={!current}
              >
                {started ? 'Run again' : 'Run triage'}
              </Button>
            )}
          </Space>
        }
      />
      {error && (
        <Alert
          type="error"
          showIcon
          title="Triage failed"
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={9}>
          <Card
            title="Graph progress"
            size="small"
            extra={elapsed ? <Tag>{(elapsed / 1000).toFixed(1)} s</Tag> : null}
          >
            {started ? (
              <ThoughtChain items={chainItems} line="dashed" />
            ) : (
              <Empty
                description="Run triage to stream the graph nodes"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Card>
          {result && (
            <Card size="small" style={{ marginTop: 16 }}>
              <Row gutter={8}>
                <Col span={8}>
                  <Statistic title="Ranked" value={result.ranked.length} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="Risks"
                    value={result.risks.length}
                    styles={{ content: { color: result.risks.length ? '#CF1322' : undefined } }}
                  />
                </Col>
                <Col span={8}>
                  <Statistic title="Approvals" value={result.proposedApprovalIds.length} />
                </Col>
              </Row>
            </Card>
          )}
        </Col>
        <Col xs={24} lg={15}>
          <Flex vertical gap={16}>
            <Card title="Shift summary" size="small">
              {summary ? (
                <Md content={summary} streaming={running && !result} />
              ) : (
                <Typography.Text type="secondary">
                  The summary streams here after the risk check.
                </Typography.Text>
              )}
            </Card>
            {result && result.risks.length > 0 && (
              <Card
                title={
                  <>
                    <WarningOutlined style={{ color: '#CF1322' }} /> Risks
                  </>
                }
                size="small"
              >
                <Flex vertical gap={8}>
                  {result.risks.map((r) => (
                    <Alert
                      key={`${r.ticketId}-${r.kind}`}
                      type={r.kind === 'combined' ? 'error' : 'warning'}
                      showIcon
                      title={
                        <Flex gap={8} align="center" wrap>
                          <Tag color={riskTag(r.kind).color}>{riskTag(r.kind).label}</Tag>
                          <Link to={`/tickets/${r.ticketId}`}>{r.ticketId}</Link>
                        </Flex>
                      }
                      description={r.message}
                    />
                  ))}
                </Flex>
              </Card>
            )}
            {result && (
              <Card title="Ranked tickets (Critical first)" size="small">
                <RowList
                  items={result.ranked.slice(0, 8)}
                  rowKey={(r) => r.ticketId}
                  render={(r, i) => {
                    const t = byId.get(r.ticketId);
                    return (
                      <ListRow
                        extra={<Tag>score {r.score.toFixed(0)}</Tag>}
                        avatar={
                          <Typography.Text strong style={{ width: 20, display: 'inline-block' }}>
                            {i + 1}
                          </Typography.Text>
                        }
                        title={
                          <Flex gap={8} align="center" wrap>
                            <Link to={`/tickets/${r.ticketId}`}>{r.ticketId}</Link>
                            {t && <span>{t.title}</span>}
                          </Flex>
                        }
                        description={
                          <Flex gap={6} wrap align="center">
                            {t && <PriorityTag value={t.priority} />}
                            {t && <StatusTag value={t.status} />}
                            {t && <StationTag value={t.station} />}
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {r.reasons.join(' · ')}
                            </Typography.Text>
                          </Flex>
                        }
                      />
                    );
                  }}
                />
              </Card>
            )}
            {interrupts.length > 0 && (
              <Card title="Proposed actions — human approval required" size="small">
                {interrupts.map((i) => (
                  <ApprovalSurface key={i.approvalId} interrupt={i} />
                ))}
              </Card>
            )}
          </Flex>
        </Col>
      </Row>
    </>
  );
}
