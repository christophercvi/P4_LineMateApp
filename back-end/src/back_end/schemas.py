from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

Role = Literal["line_cook", "sous_chef", "kitchen_manager", "admin", "service"]
StationId = Literal["grill", "pastry", "prep", "foh"]
DocCategory = Literal["recipe", "sop", "incident", "onboarding"]
FileKind = Literal["pdf", "docx", "pptx", "xlsx", "png", "jpg", "md", "txt"]
DocStatus = Literal["ready", "processing", "failed", "archived"]
Priority = Literal["critical", "high", "medium", "low"]
TicketStatus = Literal["open", "in_progress", "blocked", "resolved", "closed"]
ApprovalKind = Literal["draft_supply_order", "escalate_incident", "reassign_ticket", "create_ticket"]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


# ---------------------------------------------------------------- lookups / users


class StationOut(ApiModel):
    id: str
    name: str
    color: str
    short: str


class CrewOut(ApiModel):
    id: str
    name: str
    station: str | None
    title: str
    initials: str


class LookupsOut(ApiModel):
    stations: list[StationOut]
    crew: list[CrewOut]


class UserOut(ApiModel):
    id: str
    username: str
    display_name: str
    role: Role
    station: str | None
    crew_member_id: str | None
    email: str
    active: bool
    last_login: datetime | None


class AdminUserOut(UserOut):
    crew_title: str | None


class LoginIn(ApiModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(ApiModel):
    display_name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    station: StationId

    @field_validator("username", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v) or not any(c.isalpha() for c in v):
            raise ValueError("Password needs at least one letter and one number")
        return v


class TokenOut(ApiModel):
    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class AdminUserPatch(ApiModel):
    role: Role | None = None
    station: StationId | None | Literal[""] = None
    active: bool | None = None


# ---------------------------------------------------------------- documents


class ReviewEventOut(ApiModel):
    at: datetime
    by: str
    note: str


class DocumentBase(ApiModel):
    id: str
    title: str
    category: DocCategory
    station: str
    owner_id: str
    created_at: datetime
    last_reviewed: datetime
    source: Literal["seed", "upload"]
    uploaded_by: str | None = None
    file_name: str
    file_kind: str
    file_bytes: int
    file_url: str | None = None
    summary: str
    tags: list[str]
    status: DocStatus
    version: int
    chunk_count: int
    review_history: list[ReviewEventOut]
    stale: bool
    days_since_review: int


class DocListItem(DocumentBase):
    open_tickets: int


class DocListOut(ApiModel):
    items: list[DocListItem]
    hidden_incidents: int


class DocumentFull(DocumentBase):
    body: str
    cited_last7d: int = Field(alias="citedLast7d")


class ChunkOut(ApiModel):
    index: int
    tokens: int
    heading: str
    preview: str
    sha: str


class IngestJobOut(ApiModel):
    id: str
    doc_id: str
    file_name: str
    collection: str
    stage: Literal["queued", "parsing", "embedding", "ready", "failed"]
    progress: int
    started_at: datetime
    finished_at: datetime | None = None
    chunks: int | None = None
    error: str | None = None
    requested_by: str
    requested_by_name: str | None = None


class DocumentDetailOut(ApiModel):
    document: DocumentFull
    chunks: list[ChunkOut]
    related_tickets: list["TicketListItem"]
    jobs: list[IngestJobOut]
    embedding_model: str


class DocUploadOut(ApiModel):
    document: DocListItem
    job: IngestJobOut


class ReviewIn(ApiModel):
    note: str = ""


class BulkReviewIn(ApiModel):
    ids: list[str]


class BulkReviewOut(ApiModel):
    updated: list[str]
    skipped: list[str]


# ---------------------------------------------------------------- tickets


class TicketBase(ApiModel):
    id: str
    title: str
    description: str
    priority: Priority
    status: TicketStatus
    station: str
    assignee_id: str | None
    reporter_id: str
    related_doc_id: str | None
    created_at: datetime
    updated_at: datetime
    tags: list[str]


class TicketListItem(TicketBase):
    comment_count: int
    attachment_count: int
    doc_stale: bool
    mismatch: bool


class TicketCreateIn(ApiModel):
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(default="", max_length=4000)
    priority: Priority = "medium"
    station: StationId | None = None
    assignee_id: str | None = None
    related_doc_id: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=12)


class TicketPatchIn(ApiModel):
    status: TicketStatus | None = None
    priority: Priority | None = None
    assignee_id: str | None = None
    assignee_set: bool = False


class CommentIn(ApiModel):
    body: str = Field(min_length=1, max_length=4000)
    parent_id: str | None = None


class CommentOut(ApiModel):
    id: str
    ticket_id: str
    author_id: str
    body: str
    created_at: datetime
    parent_id: str | None


class AttachmentOut(ApiModel):
    id: str
    ticket_id: str
    name: str
    kind: str
    bytes: int
    url: str | None
    uploaded_by: str
    uploaded_at: datetime


class ActivityOut(ApiModel):
    id: str
    at: datetime
    actor_id: str
    actor_name: str
    action: str
    target: str
    target_type: Literal["ticket", "document", "approval", "agent", "user"]
    detail: str | None = None


class MismatchRow(ApiModel):
    ticket_id: str
    title: str
    priority: Priority
    status: TicketStatus
    assignee_id: str | None
    assignee_station: str | None
    doc_id: str
    doc_title: str
    doc_station: str
    doc_stale: bool
    suggested_assignee_id: str | None


class TicketPermissions(ApiModel):
    change_status: bool
    lower_or_close: bool
    raise_: bool = Field(alias="raise")
    assign: bool
    comment: bool
    attach: bool


class TicketDetailOut(ApiModel):
    ticket: TicketListItem
    comments: list[CommentOut]
    attachments: list[AttachmentOut]
    related_doc: DocListItem | None
    mismatch: MismatchRow | None
    activity: list[ActivityOut]
    permissions: TicketPermissions


# ---------------------------------------------------------------- audits / analytics


class StaleRow(ApiModel):
    doc_id: str
    title: str
    category: DocCategory
    station: str
    owner_id: str
    last_reviewed: datetime
    days_since_review: int
    open_tickets: int
    cited_last7d: int = Field(alias="citedLast7d")


class StaleAuditOut(ApiModel):
    threshold: int
    scope: str
    rows: list[StaleRow]
    excluded_incidents: list[dict[str, Any]]
    boundary: list[dict[str, Any]]
    all: list[dict[str, Any]]


class FlowOut(ApiModel):
    source: str
    target: str
    value: int


class OwnershipOut(ApiModel):
    scope: str
    rows: list[MismatchRow]
    flows: list[FlowOut]


class WorkloadRow(ApiModel):
    station: str
    open: int
    critical: int
    high: int
    medium: int
    low: int
    weighted: int
    share: float
    load_index: float
    flagged: bool


class PerDayOut(ApiModel):
    date: date
    station: str
    count: int


class WorkloadOut(ApiModel):
    scope: str
    station: str | None
    mean: float
    rows: list[WorkloadRow]
    per_day: list[PerDayOut]
    read_only: bool


class RankedTicket(ApiModel):
    ticket_id: str
    score: float
    reasons: list[str]


class TriageTeaser(RankedTicket):
    ticket: TicketBase


class DashboardKpis(ApiModel):
    open: int
    critical: int
    high: int
    stale_docs: int
    pending_approvals: int
    my_open: int
    resolution_rate: float


class DashboardAdmin(ApiModel):
    runs_today: int
    models_loaded: int
    vectors: int
    errors: int


class DashboardOut(ApiModel):
    scope: str
    kpis: DashboardKpis
    open_trend: list[int]
    closed_trend: list[int]
    my_tickets: list[TicketListItem]
    stale_cited: list[StaleRow]
    triage_teaser: list[TriageTeaser]
    activity: list[ActivityOut]
    admin: DashboardAdmin | None


# ---------------------------------------------------------------- approvals / runs


class ApprovalOut(ApiModel):
    id: str
    kind: ApprovalKind
    status: Literal["pending", "approved", "rejected"]
    title: str
    summary: str
    requested_by: str
    requested_by_name: str
    requested_at: datetime
    run_id: str
    source: str
    payload: dict[str, Any]
    decided_by: str | None = None
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    reason: str | None = None
    result: dict[str, Any] | None = None


class DecisionIn(ApiModel):
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=1000)
    quantity: int | None = Field(default=None, ge=1, le=500)


class NodeTraceOut(ApiModel):
    node: str
    started_ms: int
    duration_ms: int
    status: Literal["success", "error", "interrupted", "skipped"]
    input: dict[str, Any]
    output: dict[str, Any]


class RunOut(ApiModel):
    id: str
    graph: Literal["ask", "triage"]
    user_id: str
    user_name: str
    model: str
    started_at: datetime
    duration_ms: int
    tokens_in: int
    tokens_out: int
    status: Literal["success", "interrupted", "error"]
    question: str | None = None
    nodes: list[NodeTraceOut]


# ---------------------------------------------------------------- models / chat


class ModelOut(ApiModel):
    name: str
    family: str
    parameters: str
    quantization: str
    size_gb: float
    context_length: int
    capabilities: list[str]
    thinking: Literal["none", "toggle", "always"]
    loaded: bool
    keep_alive: str
    purpose: str
    supported: bool


class MyModelsOut(ApiModel):
    models: list[ModelOut]
    default: str | None
    vision: bool
    ollama_up: bool


class ModelLimits(ApiModel):
    max_loaded_models: int
    num_parallel: int
    host_memory_gb: float
    reserved_gb: float
    loaded_gb: float
    loaded_count: int


class AdminModelsOut(ApiModel):
    models: list[ModelOut]
    allowlist: dict[str, list[str]]
    limits: ModelLimits
    ollama_up: bool


class AllowlistIn(ApiModel):
    allowlist: dict[str, list[str]]


class HistoryTurn(ApiModel):
    role: Literal["user", "assistant"]
    content: str


class AskAttachment(ApiModel):
    name: str
    url: str | None = None
    bytes: int | None = None
    asset_id: str | None = None


class AskIn(ApiModel):
    question: str = Field(min_length=1, max_length=4000)
    model: str
    reasoning: bool = False
    conversation_id: str = Field(min_length=1, max_length=40)
    history: list[HistoryTurn] = Field(default_factory=list)
    attachments: list[AskAttachment] = Field(default_factory=list)
    ticket_id: str | None = None
    strategy: Literal["similarity", "mmr", "threshold"] | None = None


class TriageIn(ApiModel):
    model: str | None = None
    station: StationId | None = None


class ConversationOut(ApiModel):
    id: str
    title: str
    model: str
    created_at: datetime
    updated_at: datetime
    messages: int


class ConversationRenameIn(ApiModel):
    title: str = Field(min_length=1, max_length=80)


class ChatMessageOut(ApiModel):
    role: Literal["user", "assistant"]
    content: str
    extra: dict[str, Any]
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    memory_summary: str
    history: list[ChatMessageOut]


class ChatUploadOut(ApiModel):
    asset_id: str
    name: str
    url: str
    bytes: int
    kind: str


# ---------------------------------------------------------------- MCP / platform


class McpToolOut(ApiModel):
    name: str
    description: str
    access: Literal["read", "write"]
    requires_approval: bool
    roles: list[str]
    input_schema: dict[str, Any]
    sample_input: dict[str, Any]
    calls_last7d: int = Field(alias="callsLast7d")


class McpRemoteTool(ApiModel):
    name: str
    description: str
    used_by: str


class McpServerOut(ApiModel):
    id: str
    name: str
    url: str
    transport: Literal["streamable-http", "stdio", "sse"]
    status: Literal["connected", "degraded", "offline"]
    last_seen: datetime | None
    latency_ms: int
    tools: list[McpRemoteTool]


class ServiceTokenOut(ApiModel):
    id: str
    name: str
    client: str
    scopes: list[str]
    prefix: str
    created_at: datetime
    last_used: datetime | None
    expires_at: datetime
    revoked: bool


class ServiceTokenCreateIn(ApiModel):
    name: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9][a-z0-9-]+$")
    client: str = Field(min_length=2, max_length=120)
    scopes: list[Literal["mcp:read", "mcp:write"]] = Field(min_length=1)
    days: int = Field(default=90, ge=1, le=365)


class ServiceTokenCreatedOut(ServiceTokenOut):
    token: str


class McpOut(ApiModel):
    tools: list[McpToolOut]
    servers: list[McpServerOut]
    tokens: list[ServiceTokenOut] | None
    endpoint: str


class McpTryIn(ApiModel):
    input: dict[str, Any] | None = None


class McpTryOut(ApiModel):
    status: Literal["ok", "interrupted", "error"]
    requires_approval: bool
    input: dict[str, Any]
    output: Any = None
    message: str | None = None
    latency_ms: int


class CollectionOut(ApiModel):
    name: str
    embedding_model: str
    runtime: Literal["ollama", "in-process"]
    dimensions: int
    vectors: int
    documents: int
    distance: Literal["cosine"]
    last_indexed: datetime | None
    orphaned: int


class ServiceHealthOut(ApiModel):
    name: str
    status: Literal["up", "degraded", "down"]
    detail: str
    version: str


class ReindexStatus(ApiModel):
    collection: str
    progress: int


class VectorStoreOut(ApiModel):
    collections: list[CollectionOut]
    jobs: list[IngestJobOut]
    services: list[ServiceHealthOut]
    reindex: ReindexStatus | None


class ReindexIn(ApiModel):
    collection: str | None = None


DocumentDetailOut.model_rebuild()
