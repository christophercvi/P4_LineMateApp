export type Role = 'line_cook' | 'sous_chef' | 'kitchen_manager' | 'admin' | 'service';
export type StationId = 'grill' | 'pastry' | 'prep' | 'foh';

export interface Station {
  id: StationId;
  name: string;
  color: string;
  short: string;
}

export interface CrewMember {
  id: string;
  name: string;
  station: StationId | null;
  title: string;
  initials: string;
}

export interface User {
  id: string;
  username: string;
  displayName: string;
  role: Role;
  station: StationId | null;
  crewMemberId: string | null;
  email: string;
  active: boolean;
  lastLogin: string | null;
}

/** Claims inside the back-end's HS256 access token. */
export interface SessionClaims {
  sub: string;
  username: string;
  name: string;
  role: Role;
  station: StationId | null;
  crew: string | null;
  exp: number;
  iat: number;
}

export type DocCategory = 'recipe' | 'sop' | 'incident' | 'onboarding';
export type FileKind = 'pdf' | 'docx' | 'pptx' | 'xlsx' | 'png' | 'jpg' | 'md' | 'txt';
export type DocStatus = 'ready' | 'processing' | 'failed' | 'archived';

export interface ReviewEvent {
  at: string;
  by: string;
  note: string;
}

export interface LmDocument {
  id: string;
  title: string;
  category: DocCategory;
  station: StationId;
  ownerId: string;
  createdAt: string;
  lastReviewed: string;
  source: 'seed' | 'upload';
  uploadedBy?: string;
  fileName: string;
  fileKind: FileKind;
  fileBytes: number;
  fileUrl?: string | null;
  summary: string;
  body: string;
  tags: string[];
  status: DocStatus;
  version: number;
  chunkCount: number;
  reviewHistory: ReviewEvent[];
}

export type Priority = 'critical' | 'high' | 'medium' | 'low';
export type TicketStatus = 'open' | 'in_progress' | 'blocked' | 'resolved' | 'closed';

export interface Attachment {
  id: string;
  ticketId: string;
  name: string;
  kind: FileKind;
  bytes: number;
  url?: string;
  uploadedBy: string;
  uploadedAt: string;
}

export interface Ticket {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  status: TicketStatus;
  station: StationId;
  assigneeId: string | null;
  reporterId: string;
  relatedDocId: string | null;
  createdAt: string;
  updatedAt: string;
  tags: string[];
}

export interface TicketComment {
  id: string;
  ticketId: string;
  authorId: string;
  body: string;
  createdAt: string;
  parentId: string | null;
}

export interface ActivityEvent {
  id: string;
  at: string;
  actorId: string;
  action: string;
  target: string;
  targetType: 'ticket' | 'document' | 'approval' | 'agent' | 'user';
  detail?: string;
}

export type Capability = 'completion' | 'tools' | 'thinking' | 'vision' | 'embedding';
export type ThinkingMode = 'none' | 'toggle' | 'always';

export interface ModelInfo {
  name: string;
  family: string;
  parameters: string;
  quantization: string;
  sizeGb: number;
  contextLength: number;
  capabilities: Capability[];
  thinking: ThinkingMode;
  loaded: boolean;
  keepAlive: string;
  purpose: string;
  supported: boolean;
}

export type ModelAllowlist = Record<Exclude<Role, 'service'>, string[]>;

export type ApprovalKind =
  'draft_supply_order' | 'escalate_incident' | 'reassign_ticket' | 'create_ticket';
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

export interface SupplyOrderPayload {
  supplier: string;
  item: string;
  sku: string;
  quantity: number;
  unit: string;
  unitPrice: number;
  neededBy: string;
  ticketId: string;
}

export interface EscalationPayload {
  ticketId: string;
  severity: 'food_safety' | 'equipment' | 'staffing';
  notify: string[];
  holdProduct: boolean;
  note: string;
}

export interface ReassignPayload {
  ticketId: string;
  fromAssigneeId: string | null;
  toAssigneeId: string;
  reason: string;
}

export interface Approval {
  id: string;
  kind: ApprovalKind;
  status: ApprovalStatus;
  title: string;
  summary: string;
  requestedBy: string;
  requestedAt: string;
  runId: string | null;
  source: 'agent' | 'mcp' | 'user';
  payload: SupplyOrderPayload | EscalationPayload | ReassignPayload | CreateTicketPayload;
  decidedBy?: string | null;
  decidedAt?: string | null;
  reason?: string | null;
  result?: Record<string, unknown> | null;
}

export interface CreateTicketPayload {
  title: string;
  description?: string;
  priority: Priority;
  station: StationId;
  tags?: string[];
}

export type NodeStatus = 'success' | 'error' | 'interrupted' | 'skipped';

export interface NodeTrace {
  node: string;
  startedMs: number;
  durationMs: number;
  status: NodeStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface AgentRun {
  id: string;
  graph: 'ask' | 'triage';
  userId: string;
  model: string;
  startedAt: string;
  durationMs: number;
  tokensIn: number;
  tokensOut: number;
  status: 'success' | 'interrupted' | 'error' | 'aborted';
  question?: string;
  nodes: NodeTrace[];
}

export interface McpTool {
  name: string;
  description: string;
  access: 'read' | 'write';
  requiresApproval: boolean;
  roles: Role[];
  inputSchema: Record<string, unknown>;
  sampleInput: Record<string, unknown>;
  callsLast7d: number;
}

export interface McpServer {
  id: string;
  name: string;
  url: string;
  transport: 'streamable-http' | 'stdio' | 'sse';
  status: 'connected' | 'degraded' | 'offline';
  lastSeen: string | null;
  latencyMs: number | null;
  tools: { name: string; description: string; usedBy: string }[];
}

export interface ServiceToken {
  id: string;
  name: string;
  client: string;
  scopes: string[];
  prefix: string;
  createdAt: string;
  lastUsed: string | null;
  expiresAt: string;
  revoked: boolean;
}

export type IngestStage = 'queued' | 'parsing' | 'embedding' | 'ready' | 'failed';

export interface IngestJob {
  id: string;
  docId: string;
  fileName: string;
  collection: string;
  stage: IngestStage;
  progress: number;
  startedAt: string;
  finishedAt?: string | null;
  chunks?: number | null;
  error?: string | null;
  requestedBy: string;
  requestedByName?: string;
}

export interface VectorCollection {
  name: string;
  embeddingModel: string;
  runtime: 'ollama' | 'in-process';
  dimensions: number;
  vectors: number;
  documents: number;
  distance: 'cosine';
  lastIndexed: string | null;
  orphaned: number;
}

export interface ServiceHealth {
  name: string;
  status: 'up' | 'degraded' | 'down';
  detail: string;
  version: string;
}

export interface ChunkInfo {
  index: number;
  tokens: number;
  heading: string;
  preview: string;
  sha: string;
}

export interface SourceRef {
  docId: string;
  title: string;
  category: DocCategory;
  station: StationId;
  lastReviewed: string;
  daysSinceReview: number;
  stale: boolean;
  score: number;
  snippet: string;
}

export interface ChatStep {
  key: string;
  title: string;
  description?: string;
  status: 'loading' | 'success' | 'error' | 'abort';
}

export interface ChatInterrupt {
  approvalId: string;
  kind: ApprovalKind;
  title: string;
  summary: string;
  payload: SupplyOrderPayload | EscalationPayload | ReassignPayload | CreateTicketPayload;
  canApprove: boolean;
}

export interface ChatMeta {
  model: string;
  reasoning: boolean;
  thinking: ThinkingMode;
  memoryTurns: number;
  hiddenSources: number;
  runId: string;
  conversationId: string;
}

export interface ChatAttachment {
  name: string;
  url?: string;
  bytes?: number;
  assetId?: string;
}

export interface LineMateMessage {
  role: 'user' | 'assistant';
  content: string;
  reasoning?: string;
  reasoningMs?: number;
  steps?: ChatStep[];
  sources?: SourceRef[];
  interrupt?: ChatInterrupt;
  meta?: ChatMeta;
  elapsedMs?: number;
  attachments?: ChatAttachment[];
  model?: string;
  ticketId?: string;
  runId?: string;
  done?: boolean;
  aborted?: boolean;
  error?: boolean;
}

export interface AskInput {
  question: string;
  model: string;
  reasoning: boolean;
  conversationId: string;
  history: { role: 'user' | 'assistant'; content: string }[];
  attachments?: ChatAttachment[];
  ticketId?: string;
}

export interface TriageNodeEvent {
  node: string;
  status: 'loading' | 'success' | 'error';
  title: string;
  description?: string;
  durationMs?: number;
}

export type TriageRiskKind =
  'stale_sop' | 'ownership_mismatch' | 'combined' | 'shortage' | 'staffing';

export interface TriageResult {
  runId: string;
  summary: string;
  ranked: { ticketId: string; score: number; reasons: string[] }[];
  risks: { ticketId: string; kind: TriageRiskKind; message: string }[];
  proposedApprovalIds: string[];
}

export interface WorkloadRow {
  station: StationId;
  open: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  weighted: number;
  share: number;
  loadIndex: number;
  flagged: boolean;
}

export interface MismatchRow {
  ticketId: string;
  title: string;
  priority: Priority;
  status: TicketStatus;
  assigneeId: string | null;
  assigneeStation: StationId | null;
  docId: string;
  docTitle: string;
  docStation: StationId;
  docStale: boolean;
  suggestedAssigneeId: string | null;
}

export interface StaleRow {
  docId: string;
  title: string;
  category: DocCategory;
  station: StationId;
  ownerId: string;
  lastReviewed: string;
  daysSinceReview: number;
  openTickets: number;
  citedLast7d: number;
}
