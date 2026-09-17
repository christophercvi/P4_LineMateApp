"""SQLModel table definitions for the in-memory SQLite database.

Only table models live here. API responses use the camelCase schemas in `back_end.schemas`, so
columns such as `password_hash` and `token_hash` are never serialised by accident.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, TypeDecorator
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC; SQLite drops tzinfo, so it is restored on the way out."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(*, nullable: bool = False, index: bool = False, default: bool = True) -> Any:
    if nullable:
        return Field(default=None, sa_type=UTCDateTime, nullable=True, index=index)
    if default:
        return Field(default_factory=_now, sa_type=UTCDateTime, nullable=False, index=index)
    return Field(sa_type=UTCDateTime, nullable=False, index=index)


def _json_list() -> Any:
    return Field(default_factory=list, sa_type=JSON)


def _json_dict() -> Any:
    return Field(default_factory=dict, sa_type=JSON)


class Station(SQLModel, table=True):
    __tablename__ = "stations"
    id: str = Field(primary_key=True, max_length=16)
    name: str
    short: str
    color: str


class CrewMember(SQLModel, table=True):
    __tablename__ = "crew_members"
    id: str = Field(primary_key=True, max_length=16)
    name: str
    station_id: str | None = Field(default=None, foreign_key="stations.id")
    title: str
    initials: str


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: str = Field(primary_key=True, max_length=40)
    username: str = Field(unique=True, index=True)
    display_name: str
    email: str = Field(unique=True)
    role: str
    station_id: str | None = Field(default=None, foreign_key="stations.id")
    crew_member_id: str | None = Field(default=None, foreign_key="crew_members.id")
    password_hash: str
    active: bool = True
    last_login: datetime | None = _ts(nullable=True)
    created_at: datetime = _ts()


class FileAsset(SQLModel, table=True):
    """Metadata for every stored file; the bytes live under back-end/storage/<stored_path>."""

    __tablename__ = "file_assets"
    id: str = Field(primary_key=True, max_length=40)
    purpose: str  # document | attachment | chat
    owner_type: str  # document | ticket | conversation
    owner_id: str = Field(index=True)
    original_name: str
    stored_path: str
    mime_type: str
    bytes: int
    sha256: str = Field(index=True)
    uploaded_by: str | None = None
    created_at: datetime = _ts()


class Document(SQLModel, table=True):
    __tablename__ = "documents"
    id: str = Field(primary_key=True, max_length=24)
    title: str
    category: str = Field(index=True)
    station_id: str = Field(foreign_key="stations.id")
    owner_id: str = Field(foreign_key="crew_members.id")
    created_at: datetime = _ts()
    last_reviewed: datetime = _ts()
    source: str  # seed | upload
    uploaded_by: str | None = None
    file_name: str
    file_kind: str
    file_bytes: int = 0
    summary: str = ""
    body: str = ""
    tags: list[str] = _json_list()
    status: str = "processing"
    version: int = 1
    chunk_count: int = 0
    asset_id: str | None = Field(default=None, foreign_key="file_assets.id")


class ReviewEvent(SQLModel, table=True):
    __tablename__ = "review_events"
    id: int | None = Field(default=None, primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    at: datetime = _ts()
    by: str
    note: str


class DocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunks"
    id: str = Field(primary_key=True, max_length=80)  # also the Chroma id
    document_id: str = Field(foreign_key="documents.id", index=True)
    version: int
    index: int
    tokens: int
    heading: str
    text: str
    sha: str
    collection: str


class DocumentCitation(SQLModel, table=True):
    __tablename__ = "document_citations"
    id: int | None = Field(default=None, primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    at: datetime = _ts()
    run_id: str | None = None


class IngestJob(SQLModel, table=True):
    __tablename__ = "ingest_jobs"
    id: str = Field(primary_key=True, max_length=24)
    doc_id: str = Field(foreign_key="documents.id", index=True)
    file_name: str
    collection: str
    stage: str = "queued"
    progress: int = 0
    started_at: datetime = _ts()
    finished_at: datetime | None = _ts(nullable=True)
    chunks: int | None = None
    error: str | None = None
    requested_by: str


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"
    id: str = Field(primary_key=True, max_length=16)
    title: str
    description: str = ""
    priority: str = Field(index=True)
    status: str = Field(index=True)
    station_id: str = Field(foreign_key="stations.id", index=True)
    assignee_id: str | None = Field(default=None, foreign_key="crew_members.id")
    reporter_id: str
    related_doc_id: str | None = Field(default=None, foreign_key="documents.id")
    created_at: datetime = _ts()
    updated_at: datetime = _ts()
    tags: list[str] = _json_list()


class TicketComment(SQLModel, table=True):
    __tablename__ = "ticket_comments"
    id: str = Field(primary_key=True, max_length=16)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    author_id: str
    body: str
    created_at: datetime = _ts()
    parent_id: str | None = None


class Attachment(SQLModel, table=True):
    __tablename__ = "attachments"
    id: str = Field(primary_key=True, max_length=16)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    asset_id: str | None = Field(default=None, foreign_key="file_assets.id")
    name: str
    kind: str
    bytes: int
    uploaded_by: str
    uploaded_at: datetime = _ts()


class ActivityEvent(SQLModel, table=True):
    __tablename__ = "activity_events"
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = _ts(index=True)
    actor_id: str
    action: str
    target: str = Field(index=True)
    target_type: str
    detail: str | None = None


class Approval(SQLModel, table=True):
    __tablename__ = "approvals"
    id: str = Field(primary_key=True, max_length=16)
    kind: str
    status: str = Field(default="pending", index=True)
    title: str
    summary: str
    requested_by: str
    requested_at: datetime = _ts()
    run_id: str
    thread_id: str | None = None
    source: str = "agent"  # agent | mcp
    payload: dict[str, Any] = _json_dict()
    decided_by: str | None = None
    decided_at: datetime | None = _ts(nullable=True)
    reason: str | None = None
    result: dict[str, Any] | None = Field(default=None, sa_type=JSON)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"
    id: str = Field(primary_key=True, max_length=40)
    graph: str
    user_id: str = Field(index=True)
    model: str
    started_at: datetime = _ts()
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    status: str = "success"
    question: str | None = None
    nodes: list[dict[str, Any]] = _json_list()


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    id: str = Field(primary_key=True, max_length=40)
    user_id: str = Field(foreign_key="users.id", index=True)
    title: str
    model: str
    memory_summary: str = ""
    memory_folded: int = 0  # messages already folded into memory_summary
    created_at: datetime = _ts()
    updated_at: datetime = _ts()


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id", index=True)
    role: str
    content: str = ""
    extra: dict[str, Any] = _json_dict()
    created_at: datetime = _ts()


class ModelAllowlist(SQLModel, table=True):
    __tablename__ = "model_allowlist"
    role: str = Field(primary_key=True, max_length=24)
    # Block list, not an allow list: every model Ollama reports is offered to the role unless an
    # admin hid it here, so a newly pulled model appears in the dropdown with no config change.
    blocked: list[str] = _json_list()


class ServiceToken(SQLModel, table=True):
    __tablename__ = "service_tokens"
    id: str = Field(primary_key=True, max_length=24)
    name: str
    client: str
    scopes: list[str] = _json_list()
    prefix: str = Field(index=True)
    token_hash: str
    user_id: str = Field(foreign_key="users.id")
    created_at: datetime = _ts()
    last_used: datetime | None = _ts(nullable=True)
    expires_at: datetime = _ts(default=False)
    revoked: bool = False


class McpToolCall(SQLModel, table=True):
    __tablename__ = "mcp_tool_calls"
    id: int | None = Field(default=None, primary_key=True)
    tool: str = Field(index=True)
    caller: str
    at: datetime = _ts(index=True)
    status: str = "ok"
    latency_ms: float = 0


class Counter(SQLModel, table=True):
    __tablename__ = "counters"
    name: str = Field(primary_key=True, max_length=32)
    value: int = 0
