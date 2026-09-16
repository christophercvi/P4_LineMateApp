from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "LineMate"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "info"
    log_json: bool = False

    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    # Host headers accepted by the /mcp endpoint (DNS-rebinding protection). "*" ports are allowed.
    mcp_allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost:*", "127.0.0.1:*", "localhost", "127.0.0.1", "testserver"])

    database_url: str = "sqlite+aiosqlite:///:memory:"

    jwt_secret: str = "change-me-in-.env-linemate-local-secret"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 8 * 60
    file_url_ttl_seconds: int = 60 * 60

    seed_on_startup: bool = True
    seed_password: str = "Hearthline#2026"
    vectorize_on_startup: bool = True

    storage_dir: Path = BACKEND_ROOT / "storage"
    chroma_dir: Path = BACKEND_ROOT / "chroma_db"
    parse_cache_dir: Path | None = None  # default: <storage_dir>/.cache/parsed
    seed_data_dir: Path = BACKEND_ROOT / "seed_data"
    seed_documents_dir: Path = BACKEND_ROOT / "seed_documents"

    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    chat_model: str = "llama3.2:3b"
    supported_models: list[str] = Field(default_factory=lambda: ["llama3.2:3b", "qwen3:4b-q4_K_M", "gemma4:e2b"])
    ollama_keep_alive: str = "15m"
    ollama_timeout_seconds: float = 600.0
    num_ctx: int = 8192
    max_answer_tokens: int = 900

    vision_enabled: bool = True
    vision_model: str = "nomic-ai/nomic-embed-vision-v1.5"

    stale_days: int = 180
    chunk_tokens: int = 380
    chunk_overlap: int = 60
    retrieval_k: int = 4
    retrieval_strategy: Literal["similarity", "mmr", "threshold"] = "mmr"
    score_threshold: float = 0.42  # strict 'threshold' strategy
    min_relevance: float = 0.35  # below this a chunk is not used as context
    memory_window: int = 6

    max_upload_mb: int = 20

    mcp_external_enabled: bool = True
    mcp_call_timeout_seconds: float = 20.0

    @field_validator("storage_dir", "chroma_dir", "parse_cache_dir", "seed_data_dir", "seed_documents_dir", mode="after")
    @classmethod
    def _relative_to_backend(cls, value: Path | None) -> Path | None:
        """Relative paths in .env (e.g. STORAGE_DIR=storage) are taken from the back-end folder, not the shell's cwd."""
        if value is None or value.is_absolute():
            return value
        return (BACKEND_ROOT / value).resolve()

    @computed_field
    @property
    def docs_collection(self) -> str:
        return "documents__nomic-embed-text-v1.5"

    @computed_field
    @property
    def photos_collection(self) -> str:
        return "photos__nomic-embed-vision-v1.5"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
