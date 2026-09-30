import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Card,
  Col,
  Descriptions,
  Flex,
  Row,
  Segmented,
  Skeleton,
  Table,
  Tag,
  Typography,
} from 'antd';
import { Mermaid } from '@ant-design/x';
import { api } from '@/api/client';
import { PageHeader } from '@/components/PageHeader';

const ASK_GRAPH = `flowchart TD
  START([START]) --> classify_intent
  classify_intent -->|procedure · ticket · supply · photo| retrieve
  retrieve -.->|nomic-embed-text, role + station filter| docs[(Chroma: documents)]
  retrieve -.->|photo attached: OCR + nomic-embed-vision| photos[(Chroma: photos)]
  retrieve -.->|supply question| supplier[(Supplier Inventory MCP)]
  retrieve --> grade_documents
  grade_documents -->|relevant context| load_model
  grade_documents -->|nothing relevant| no_answer
  load_model --> generate
  generate --> check_citations
  no_answer --> check_citations
  check_citations --> propose_action
  propose_action -->|answer only| END([END])
  propose_action -->|supply order · ticket · reassignment| human_approval{{"human_approval (interrupt)"}}
  human_approval -->|decision from Approvals| execute_action
  execute_action --> END
  classDef interrupt fill:#FFF7E6,stroke:#FA8C16,stroke-width:2px;
  classDef ext fill:#E6F4FF,stroke:#1677FF;
  class human_approval interrupt;
  class docs,photos,supplier ext;`;

const TRIAGE_GRAPH = `flowchart TD
  START([START]) --> load_tickets
  load_tickets --> rank
  rank --> enrich_with_docs
  enrich_with_docs -.->|get_roster · find_cover| scheduler[(Shift Scheduler MCP)]
  enrich_with_docs --> check_supplies
  check_supplies -.->|check_stock| supplier[(Supplier Inventory MCP)]
  check_supplies --> detect_risks
  detect_risks --> propose_actions
  propose_actions -.->|price_quote| supplier
  propose_actions --> summarize
  summarize -->|no proposals| END([END])
  summarize -->|supply order · reassignment · escalation| human_approval{{"human_approval (interrupt)"}}
  human_approval -->|more decisions pending| human_approval
  human_approval -->|all decided| execute_decision
  execute_decision --> END
  classDef interrupt fill:#FFF7E6,stroke:#FA8C16,stroke-width:2px;
  classDef ext fill:#E6F4FF,stroke:#1677FF;
  class human_approval interrupt;
  class scheduler,supplier ext;`;

const STATE = {
  ask: [
    {
      key: 'actor',
      type: 'dict',
      note: 'Signed-in user, role and station; every retrieval is filtered by them',
    },
    { key: 'question', type: 'str', note: 'Current question; rewritten into the search query' },
    {
      key: 'model / reasoning',
      type: 'str, bool',
      note: 'Chosen Ollama model and whether it is asked to think',
    },
    {
      key: 'history / summary',
      type: 'list[dict], str',
      note: 'Recent turns plus a rolling summary of older ones',
    },
    {
      key: 'attachments',
      type: 'list[dict]',
      note: 'Uploaded photos: OCR text, photo matches and images for vision models',
    },
    {
      key: 'hits / sources',
      type: 'list[dict]',
      note: 'Retrieved chunks and the citations shown with the answer',
    },
    { key: 'hidden', type: 'int', note: 'Matches the role cannot read (counted, never shown)' },
    {
      key: 'stock',
      type: 'dict | None',
      note: 'Live stock from the supplier MCP server for supply questions',
    },
    {
      key: 'approval_id / decision',
      type: 'str | None, dict | None',
      note: 'Pending approval and the manager decision that resumes the run',
    },
    {
      key: 'trace',
      type: 'list[dict] (append)',
      note: 'Per-node timings and outputs shown under Runs',
    },
  ],
  triage: [
    {
      key: 'actor / station',
      type: 'dict, str | None',
      note: 'Sous Chef: own station; Kitchen Manager: every station',
    },
    {
      key: 'open_ids / ranked',
      type: 'list[str], list[dict]',
      note: 'Open tickets ranked by priority, age and document staleness',
    },
    {
      key: 'docs',
      type: 'dict[str, dict]',
      note: 'Owning SOP for each ticket with its review status',
    },
    {
      key: 'rosters / stock',
      type: 'list[dict], dict[str, dict]',
      note: 'Shift rosters and supplier stock fetched over MCP',
    },
    {
      key: 'risks',
      type: 'list[dict]',
      note: 'stale_sop, ownership_mismatch, combined, shortage, staffing',
    },
    {
      key: 'proposal_ids',
      type: 'list[str]',
      note: 'Approvals created for write actions; each pauses the graph',
    },
    { key: 'summary', type: 'str', note: 'Markdown shift summary written by the planner model' },
    {
      key: 'decisions / trace',
      type: 'list[dict] (append)',
      note: 'Manager decisions and per-node traces',
    },
  ],
};

export default function AgentGraph() {
  const [graph, setGraph] = useState<'ask' | 'triage'>('ask');
  const [view, setView] = useState<'overview' | 'compiled'>('compiled');
  const compiled = useQuery({
    queryKey: ['agent-graphs'],
    queryFn: () => api<{ ask: string; triage: string }>('/api/agent/graphs'),
    staleTime: Infinity,
  });
  return (
    <>
      <PageHeader
        title="Agent graph"
        subtitle="LangGraph state graphs compiled with a checkpointer; write tools pause at interrupt() for human approval"
        extra={
          <Segmented
            value={graph}
            onChange={(v) => setGraph(v as 'ask' | 'triage')}
            options={[
              { label: 'Ask graph', value: 'ask' },
              { label: 'Triage graph', value: 'triage' },
            ]}
          />
        }
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card
            size="small"
            title={
              graph === 'ask'
                ? 'ask_graph — retrieval-augmented Q&A'
                : 'triage_graph — shift triage agent'
            }
            extra={
              <Segmented
                size="small"
                value={view}
                onChange={(v) => setView(v as 'overview' | 'compiled')}
                options={[
                  { label: 'Overview', value: 'overview' },
                  { label: 'Compiled', value: 'compiled' },
                ]}
              />
            }
          >
            {view === 'compiled' && compiled.isLoading && <Skeleton active />}
            {view === 'compiled' && compiled.isError && (
              <Alert
                type="warning"
                showIcon
                title="The compiled graph could not be loaded from the server."
              />
            )}
            {(view === 'overview' || compiled.data) && (
              <Mermaid
                key={`${graph}-${view}`}
                header={null}
                actions={{ enableZoom: true, enableDownload: true, enableCopy: true }}
              >
                {view === 'compiled' && compiled.data
                  ? compiled.data[graph]
                  : graph === 'ask'
                    ? ASK_GRAPH
                    : TRIAGE_GRAPH}
              </Mermaid>
            )}
            <Typography.Paragraph
              type="secondary"
              style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}
            >
              {view === 'compiled'
                ? 'Generated by LangGraph from the running back-end (draw_mermaid of the compiled graph).'
                : 'Annotated overview of the same nodes and edges, including where each step reads or writes data.'}
            </Typography.Paragraph>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card size="small" title="Graph state (TypedDict)">
            <Table
              size="small"
              rowKey="key"
              pagination={false}
              dataSource={STATE[graph]}
              columns={[
                {
                  title: 'Key',
                  dataIndex: 'key',
                  width: 170,
                  render: (k: string, row: { type: string }) => (
                    <Flex vertical gap={2}>
                      <Typography.Text code style={{ whiteSpace: 'nowrap', fontSize: 12 }}>
                        {k}
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {row.type}
                      </Typography.Text>
                    </Flex>
                  ),
                },
                { title: 'Purpose', dataIndex: 'note' },
              ]}
            />
          </Card>
          <Card size="small" title="Runtime" style={{ marginTop: 16 }}>
            <Descriptions
              size="small"
              column={1}
              items={[
                {
                  key: 'cp',
                  label: 'Checkpointer',
                  children: 'InMemorySaver, one thread per run, resumable after interrupt()',
                },
                {
                  key: 'st',
                  label: 'Streaming',
                  children: (
                    <>
                      stream_mode <Tag>custom</Tag>
                      <Tag>updates</Tag> → SSE
                    </>
                  ),
                },
                {
                  key: 'md',
                  label: 'Model',
                  children:
                    graph === 'ask'
                      ? 'Chosen per question from the models the role may use'
                      : 'Chosen per run; needs a model with tool support',
                },
                {
                  key: 'hitl',
                  label: 'Human in the loop',
                  children: 'interrupt() → Approvals → Command(resume=decision)',
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}
