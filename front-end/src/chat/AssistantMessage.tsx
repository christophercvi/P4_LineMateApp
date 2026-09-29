import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Flex, Tag, Tooltip, Typography } from 'antd';
import {
  BulbOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  HistoryOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { Sources, Think, ThoughtChain } from '@ant-design/x';
import type { LineMateMessage } from '@/api/types';
import { Md } from '@/components/Md';
import { CATEGORY, STALE_DAYS } from '@/theme/tokens';
import { ApprovalSurface } from './ApprovalCard';

const secs = (ms?: number) => (ms ? `${(ms / 1000).toFixed(1)} s` : '');

export function AssistantMessage({ msg, streaming }: { msg: LineMateMessage; streaming: boolean }) {
  const navigate = useNavigate();
  const [thinkOpen, setThinkOpen] = useState<boolean | null>(null);
  const thinking = streaming && !!msg.reasoning && !msg.content;
  const stale = (msg.sources ?? []).filter((s) => s.stale);
  const steps = msg.steps ?? [];
  const stepsDone = steps.length > 0 && steps.every((s) => s.status === 'success');

  return (
    <Flex vertical gap={10} style={{ minWidth: 0, width: '100%' }}>
      {msg.meta && (
        <Flex gap={6} wrap align="center">
          <Tag icon={<BulbOutlined />} color="volcano" variant="outlined">
            {msg.meta.model}
          </Tag>
          {msg.meta.reasoning ? (
            <Tag color="purple" variant="outlined">
              reasoning on
            </Tag>
          ) : (
            <Tag variant="outlined">no reasoning</Tag>
          )}
          {msg.meta.memoryTurns > 0 && (
            <Tooltip title="Earlier turns in this chat were sent as context">
              <Tag icon={<HistoryOutlined />} variant="outlined">
                memory · {msg.meta.memoryTurns} turn{msg.meta.memoryTurns > 1 ? 's' : ''}
              </Tag>
            </Tooltip>
          )}
          {msg.elapsedMs && (
            <Tag icon={<ClockCircleOutlined />} variant="outlined">
              {secs(msg.elapsedMs)}
            </Tag>
          )}
        </Flex>
      )}
      {steps.length > 0 && (
        <ThoughtChain
          line="dashed"
          defaultExpandedKeys={[]}
          items={steps.map((s) => ({
            key: s.key,
            title: s.title,
            description: s.description,
            status: s.status,
            blink: s.status === 'loading',
          }))}
          styles={{ root: { opacity: stepsDone ? 0.8 : 1 } }}
        />
      )}
      {msg.reasoning && (
        <Think
          title={
            thinking
              ? 'Reasoning…'
              : `Reasoning${msg.reasoningMs ? ` (${secs(msg.reasoningMs)})` : ''}`
          }
          loading={thinking}
          blink={thinking}
          expanded={thinkOpen ?? thinking}
          onExpand={(v: boolean) => setThinkOpen(v)}
        >
          <Typography.Paragraph
            type="secondary"
            style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 13 }}
          >
            {msg.reasoning}
          </Typography.Paragraph>
        </Think>
      )}
      {msg.content && <Md content={msg.content} streaming={streaming} citations />}
      {msg.aborted && (
        <Typography.Text type="secondary" italic>
          Stopped.
        </Typography.Text>
      )}
      {stale.length > 0 && !streaming && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          title={`${stale.map((s) => s.docId).join(', ')} ${stale.length > 1 ? 'were' : 'was'} last reviewed ${stale.map((s) => `${s.daysSinceReview}`).join(' / ')} days ago`}
          description={`That is past the ${STALE_DAYS}-day review policy. Confirm the steps with your Sous Chef.`}
        />
      )}
      {(msg.sources?.length ?? 0) > 0 && (
        <Sources
          title={`${msg.sources!.length} source${msg.sources!.length > 1 ? 's' : ''}`}
          defaultExpanded={false}
          items={msg.sources!.map((s, i) => ({
            key: s.docId,
            title: `[${i + 1}] ${s.title}`,
            icon: <FileTextOutlined />,
            description: (
              <span>
                {s.docId} · {CATEGORY[s.category].label} ·{' '}
                {s.stale ? (
                  <Typography.Text type="danger">
                    last reviewed {s.daysSinceReview} days ago
                  </Typography.Text>
                ) : (
                  `reviewed ${s.daysSinceReview} days ago`
                )}{' '}
                · score {s.score.toFixed(2)}
              </span>
            ),
          }))}
          onClick={(item) => item.key && navigate(`/documents/${String(item.key)}`)}
        />
      )}
      {msg.interrupt && <ApprovalSurface interrupt={msg.interrupt} />}
    </Flex>
  );
}
