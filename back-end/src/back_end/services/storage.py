"""Stores uploaded and seeded files and records them in the `file_assets` table.

Layout (relative to back-end/):
  storage/documents/<doc-id>/v<version>/<file>      knowledge-base uploads (one folder per version)
  storage/attachments/<ticket-id>/<asset-id>-<file>  ticket attachments
  storage/chat/<conversation-id>/<asset-id>-<file>   files attached to a chat question
  seed_documents/<category>/<file>                   seed documents (registered in place, never copied)

The browser never gets a filesystem path: it receives `/api/files/<asset-id>?exp=&sig=` URLs that
are HMAC-signed and short-lived, so <img> tags and downloads work without exposing the JWT.
"""

import hashlib
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from back_end.core.config import BACKEND_ROOT
from back_end.core.errors import PayloadTooLarge, Unprocessable
from back_end.core.security import signed_file_url
from back_end.db.models import FileAsset

KIND_BY_EXT = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".webp": "png",
}
IMAGE_KINDS = {"png", "jpg"}
DOCUMENT_KINDS = {"pdf", "docx", "pptx", "xlsx", "md", "txt", "png", "jpg"}

# libmagic answers for each kind (Office files are zip containers; markdown is plain text).
MAGIC_OK = {
    "pdf": ("application/pdf",),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/zip"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"),
    "md": ("text/",),
    "txt": ("text/",),
    "png": ("image/png", "image/webp"),
    "jpg": ("image/jpeg",),
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    stem = Path(name).name.strip() or "file"
    cleaned = _SAFE.sub("-", stem).strip("-.") or "file"
    return cleaned[:120]


def kind_for(name: str) -> str | None:
    return KIND_BY_EXT.get(Path(name).suffix.lower())


def sniff_mime(data: bytes) -> str:
    try:
        import magic

        return magic.from_buffer(data[:8192], mime=True)
    except Exception:  # noqa: BLE001 - libmagic missing: fall back to the extension
        return "application/octet-stream"


@dataclass(slots=True)
class StoredFile:
    asset: FileAsset
    path: Path
    kind: str


class FileStore:
    def __init__(self, storage_dir: Path, max_bytes: int, root: Path = BACKEND_ROOT):
        self.root = root
        self.storage_dir = storage_dir
        self.max_bytes = max_bytes
        storage_dir.mkdir(parents=True, exist_ok=True)

    def validate(self, name: str, data: bytes, allowed: set[str]) -> str:
        kind = kind_for(name)
        if kind is None or kind not in allowed:
            raise Unprocessable(f"Unsupported file type '{Path(name).suffix or name}'. Allowed: {', '.join(sorted(allowed))}")
        if len(data) == 0:
            raise Unprocessable("The file is empty")
        if len(data) > self.max_bytes:
            raise PayloadTooLarge(f"File is larger than {self.max_bytes // (1024 * 1024)} MB")
        mime = sniff_mime(data)
        if mime != "application/octet-stream" and not any(mime.startswith(m) for m in MAGIC_OK[kind]):
            raise Unprocessable(f"'{name}' does not look like a .{kind} file (detected {mime})")
        return kind

    def save(
        self,
        *,
        data: bytes,
        original_name: str,
        purpose: str,
        owner_type: str,
        owner_id: str,
        subdir: str,
        uploaded_by: str | None,
        allowed: set[str],
        asset_id: str | None = None,
    ) -> StoredFile:
        kind = self.validate(original_name, data, allowed)
        asset_id = asset_id or f"fa_{uuid.uuid4().hex[:16]}"
        folder = self.storage_dir / subdir
        folder.mkdir(parents=True, exist_ok=True)
        fname = safe_name(original_name) if purpose == "document" else f"{asset_id}-{safe_name(original_name)}"
        path = folder / fname
        path.write_bytes(data)
        asset = FileAsset(
            id=asset_id,
            purpose=purpose,
            owner_type=owner_type,
            owner_id=owner_id,
            original_name=Path(original_name).name,
            stored_path=self._stored(path),
            mime_type=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            uploaded_by=uploaded_by,
        )
        return StoredFile(asset, path, kind)

    def register(
        self, path: Path, *, purpose: str, owner_type: str, owner_id: str, asset_id: str, uploaded_by: str | None = None
    ) -> FileAsset:
        data = path.read_bytes()
        return FileAsset(
            id=asset_id,
            purpose=purpose,
            owner_type=owner_type,
            owner_id=owner_id,
            original_name=path.name,
            stored_path=self._stored(path),
            mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            uploaded_by=uploaded_by,
        )

    def _stored(self, path: Path) -> str:
        """Paths under the back-end folder are stored relative to it; a storage folder configured
        elsewhere (STORAGE_DIR) is stored as an absolute path."""
        path = path.resolve()
        root = self.root.resolve()
        return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()

    def path_of(self, asset: FileAsset) -> Path:
        stored = Path(asset.stored_path)
        p = (stored if stored.is_absolute() else self.root / stored).resolve()
        if not (p.is_relative_to(self.root.resolve()) or p.is_relative_to(self.storage_dir.resolve())):
            raise Unprocessable("Invalid stored path")
        return p

    @staticmethod
    def url(asset_id: str | None) -> str | None:
        return signed_file_url(asset_id) if asset_id else None
