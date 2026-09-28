import { Flex, Progress, Steps, Typography } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';
import type { IngestJob } from '@/api/types';

const ORDER = ['queued', 'parsing', 'embedding', 'ready'] as const;

export function IngestSteps({ job, compact = false }: { job: IngestJob; compact?: boolean }) {
  const failed = job.stage === 'failed';
  const current = failed
    ? job.progress < 45
      ? 1
      : 2
    : ORDER.indexOf(job.stage as (typeof ORDER)[number]);
  const image = /\.(png|jpe?g)$/i.test(job.fileName);
  const items = [
    { title: 'Queued', content: compact ? undefined : 'Saved to uploads/, hashed' },
    {
      title: 'Parsing',
      content: compact ? undefined : image ? 'OCR + nomic-embed-vision' : 'unstructured → elements',
    },
    {
      title: 'Embedding',
      content: compact
        ? undefined
        : image
          ? 'nomic-embed-text + vision'
          : 'nomic-embed-text:v1.5 → Chroma',
    },
    {
      title: failed ? 'Failed' : 'Ready',
      content: compact
        ? undefined
        : failed
          ? 'See error below'
          : `${job.chunks ?? '…'} chunks searchable`,
    },
  ].map((it, i) => ({
    ...it,
    icon: i === current && !failed && job.stage !== 'ready' ? <LoadingOutlined /> : undefined,
  }));
  return (
    <Flex vertical gap={8}>
      <Steps
        size="small"
        current={current}
        status={failed ? 'error' : job.stage === 'ready' ? 'finish' : 'process'}
        items={items}
      />
      <Progress
        percent={job.progress}
        size="small"
        status={failed ? 'exception' : job.stage === 'ready' ? 'success' : 'active'}
      />
      {failed && <Typography.Text type="danger">{job.error}</Typography.Text>}
    </Flex>
  );
}
