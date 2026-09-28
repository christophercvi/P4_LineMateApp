import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { IngestJob } from '@/api/types';

/** Polls an ingestion job until it is ready or failed, then refreshes document queries. */
export function useIngestJob(jobId: string | null | undefined) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ['ingest', jobId],
    queryFn: () => api<IngestJob>(`/api/ingest/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const s = query.state.data?.stage;
      return s === 'ready' || s === 'failed' ? false : 800;
    },
  });
  const stage = q.data?.stage;
  useEffect(() => {
    if (stage === 'ready' || stage === 'failed') {
      qc.invalidateQueries({ queryKey: ['documents'] });
      qc.invalidateQueries({ queryKey: ['document'] });
    }
  }, [stage, qc]);
  return q;
}
