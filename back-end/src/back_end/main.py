"""LineMate API: FastAPI application factory, service wiring and the Granian entry point.

    uv run back-end                     # Granian ASGI server on settings.host:settings.port
    uv run granian --interface asgi back_end.main:app

On startup the in-memory SQLite database is created and seeded, then the seed documents are
vectorized into Chroma (back-end/chroma_db) in the background; unchanged chunks are skipped by hash.
"""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from back_end.agents.ask_graph import build_ask_graph
from back_end.agents.triage_graph import build_triage_graph
from back_end.api.routers import (
    admin,
    agent,
    approvals,
    audits,
    auth,
    dashboard,
    documents,
    files,
    mcp,
    models,
    tickets,
)
from back_end.core.config import Settings, get_settings
from back_end.core.errors import install_error_handlers
from back_end.core.logging import configure_logging, get_logger
from back_end.db.engine import init_database
from back_end.mcp_server import McpBearerAuth, create_mcp_server
from back_end.services.container import ChatFactory, Services, ollama_chat_factory, set_services
from back_end.services.embeddings import NomicTextEmbeddings, VisionEmbedder
from back_end.services.mcp_clients import ExternalMcp
from back_end.services.ollama import OllamaService
from back_end.services.storage import FileStore
from back_end.services.vectorstore import VectorStore

log = get_logger(__name__)

DOCS_COLLECTION = "documents__nomic-embed-text-v1.5"
PHOTOS_COLLECTION = "photos__nomic-embed-vision-v1.5"


def build_services(
    settings: Settings,
    *,
    text_embeddings: Any | None = None,
    vision: Any | None = None,
    chat: ChatFactory | None = None,
    mcp_mode: str | None = None,
    ollama_transport: httpx2.AsyncBaseTransport | None = None,
) -> Services:
    """Create every long-lived service. Tests pass fakes for Ollama, embeddings and the vision model."""
    db = init_database(settings.database_url)
    embeddings = text_embeddings or NomicTextEmbeddings(settings.ollama_base_url, settings.embedding_model)
    svc = Services(
        settings=settings,
        db=db,
        files=FileStore(settings.storage_dir, settings.max_upload_mb * 1024 * 1024),
        vectors=VectorStore(
            settings.chroma_dir,
            embeddings,
            docs_collection=DOCS_COLLECTION,
            photos_collection=PHOTOS_COLLECTION,
            embedding_model_name=settings.embedding_model,
        ),
        ollama=OllamaService(
            settings.ollama_base_url,
            supported=settings.supported_models,
            keep_alive=settings.ollama_keep_alive,
            timeout=settings.ollama_timeout_seconds,
            transport=ollama_transport,
        ),
        text_embeddings=embeddings,
        vision=vision or VisionEmbedder(settings.vision_model, enabled=settings.vision_enabled),
        chat=chat or ollama_chat_factory(settings),
        mcp=ExternalMcp(mode=mcp_mode or ("stdio" if settings.mcp_external_enabled else "off"), timeout=settings.mcp_call_timeout_seconds),
    )
    svc.graphs = {"ask": build_ask_graph(svc), "triage": build_triage_graph(svc)}
    return svc


async def startup_services(
    *,
    seed_vectors: bool | None = None,
    settings: Settings | None = None,
    factory: Callable[[Settings], Services] | None = None,
) -> Services:
    """Create the services, the schema and the seed data. Used by the app lifespan, the seed CLI and stdio MCP."""
    from back_end.seed import queue_ingest_jobs, seed_database, vectorize_seed

    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    svc = (factory or build_services)(settings)
    set_services(svc)
    await svc.db.create_all()
    if settings.seed_on_startup:
        report = await seed_database(svc)
        svc.seed_status = {"state": "database", "counts": report.counts}
        log.info("seeded_database", **report.counts)
    vectorize = settings.vectorize_on_startup if seed_vectors is None else seed_vectors
    if settings.seed_on_startup and vectorize:
        job_ids = await queue_ingest_jobs(svc)

        async def run() -> None:
            svc.seed_status = {**svc.seed_status, "state": "vectorizing"}
            try:
                result = await vectorize_seed(svc, job_ids)
                svc.seed_status = {**svc.seed_status, "state": "ready", "vectors": result}
                log.info("seeded_vectors", **result)
            except Exception as exc:  # noqa: BLE001 - the API keeps working; jobs show the error
                svc.seed_status = {**svc.seed_status, "state": "failed", "error": str(exc)}
                log.error("seed_vectors_failed", error=repr(exc))

        svc.spawn(run(), name="vectorize-seed")
    elif settings.seed_on_startup:
        svc.seed_status = {**svc.seed_status, "state": "ready"}
    return svc


def create_app(settings: Settings | None = None, *, factory: Callable[[Settings], Services] | None = None) -> FastAPI:
    settings = settings or get_settings()
    mcp_server = create_mcp_server()
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.cors_origins,
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = await startup_services(settings=settings, factory=factory)
        app.state.services = svc
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                await svc.aclose()
                set_services(None)

    app = FastAPI(
        title="LineMate API",
        version="1.0.0",
        summary="Kitchen knowledge base, tickets and AI assistant for a restaurant line",
        lifespan=lifespan,
    )
    app.state.mcp_server = mcp_server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    for r in (auth, dashboard, documents, tickets, audits, approvals, models, agent, mcp, admin, files):
        app.include_router(r.router)
    # Streamable HTTP MCP endpoint at /mcp (bearer token required). Mounted last so /api routes win.
    app.mount("/", McpBearerAuth(mcp_app))
    return app


app = create_app()


def main() -> None:
    from granian import Granian
    from granian.constants import Interfaces

    s = get_settings()
    Granian("back_end.main:app", address=s.host, port=s.port, interface=Interfaces.ASGI).serve()


if __name__ == "__main__":
    main()
