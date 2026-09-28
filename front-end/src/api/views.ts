import type {
  ActivityEvent,
  AgentRun,
  Approval,
  Attachment,
  ChunkInfo,
  IngestJob,
  LmDocument,
  MismatchRow,
  ModelAllowlist,
  ModelInfo,
  McpServer,
  McpTool,
  ServiceHealth,
  ServiceToken,
  StaleRow,
  StationId,
  Ticket,
  TicketComment,
  User,
  VectorCollection,
  WorkloadRow,
} from './types';

export type DocListItem = Omit<LmDocument, 'body'> & {
  stale: boolean;
  daysSinceReview: number;
  openTickets: number;
};
export type TicketListItem = Ticket & {
  commentCount: number;
  attachmentCount: number;
  docStale: boolean;
  mismatch: boolean;
};

export interface DocumentDetailView {
  document: LmDocument & { stale: boolean; daysSinceReview: number; citedLast7d: number };
  chunks: ChunkInfo[];
  relatedTickets: TicketListItem[];
  jobs: IngestJob[];
  embeddingModel: string;
}

export interface TicketDetailView {
  ticket: TicketListItem;
  comments: TicketComment[];
  attachments: Attachment[];
  relatedDoc: DocListItem | null;
  mismatch: MismatchRow | null;
  activity: (ActivityEvent & { actorName: string })[];
  permissions: {
    changeStatus: boolean;
    lowerOrClose: boolean;
    raise: boolean;
    assign: boolean;
    comment: boolean;
    attach: boolean;
  };
}

export interface StaleAuditView {
  threshold: number;
  scope: string;
  rows: StaleRow[];
  excludedIncidents: { docId: string; title: string; days: number }[];
  boundary: { docId: string; title: string }[];
  all: { docId: string; title: string; station: StationId; days: number }[];
}

export interface OwnershipView {
  scope: string;
  rows: MismatchRow[];
  flows: { source: string; target: string; value: number }[];
}

export interface WorkloadView {
  scope: string;
  station: StationId | null;
  mean: number;
  rows: WorkloadRow[];
  perDay: { date: string; station: string; count: number }[];
  readOnly: boolean;
}

export type ApprovalView = Approval & { requestedByName: string; decidedByName?: string | null };
export type RunView = AgentRun & { userName: string };
export interface McpView {
  tools: McpTool[];
  servers: McpServer[];
  tokens: ServiceToken[] | null;
  endpoint: string;
}
export type AdminUserView = User & { crewTitle: string | null };
export interface AdminModelsView {
  models: ModelInfo[];
  allowlist: ModelAllowlist;
  limits: {
    maxLoadedModels: number;
    numParallel: number;
    hostMemoryGb: number;
    reservedGb: number;
    loadedGb: number;
    loadedCount: number;
  };
  ollamaUp: boolean;
}
export interface VectorStoreView {
  collections: VectorCollection[];
  jobs: (IngestJob & { requestedByName: string })[];
  services: ServiceHealth[];
  reindex: { collection: string; progress: number } | null;
}
export interface MyModelsView {
  models: ModelInfo[];
  default: string | null;
  vision: boolean;
  ollamaUp: boolean;
}

export interface ConversationView {
  id: string;
  title: string;
  model: string;
  createdAt: string;
  updatedAt: string;
  messages: number;
}

export interface ConversationDetailView extends ConversationView {
  memorySummary: string | null;
  history: {
    role: 'user' | 'assistant';
    content: string;
    extra: Record<string, unknown>;
    createdAt: string;
  }[];
}

export interface TokenResponse {
  token: string;
  tokenType: string;
  expiresIn: number;
  user: User;
}
