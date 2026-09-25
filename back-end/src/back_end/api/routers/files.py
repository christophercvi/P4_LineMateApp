"""Serves stored files (uploaded documents, ticket photos, chat attachments, seed files).

Browsers load images with plain `<img src>` requests that cannot carry the JWT, so the API hands out
short-lived HMAC-signed URLs (`/api/files/<id>?exp=...&sig=...`) only to users allowed to see the
owning document or ticket. The signature is the permission check here.
"""

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from back_end.api.deps import SvcDep
from back_end.core.errors import Forbidden, NotFound
from back_end.core.security import verify_file_signature
from back_end.db import models as m

router = APIRouter(prefix="/api", tags=["files"])

INLINE = ("image/", "application/pdf", "text/")


@router.get("/files/{asset_id}", summary="Download a stored file through a signed URL", response_class=FileResponse)
async def get_file(
    asset_id: str,
    svc: SvcDep,
    exp: Annotated[int, Query()],
    sig: Annotated[str, Query(min_length=16, max_length=128)],
    download: bool = False,
) -> FileResponse:
    if not verify_file_signature(asset_id, exp, sig):
        raise Forbidden("This file link has expired. Reload the page to get a fresh link.")
    async with svc.db.session() as s:
        asset = await s.get(m.FileAsset, asset_id)
    if asset is None:
        raise NotFound("File")
    path = svc.files.path_of(asset)
    if not path.is_file():
        raise NotFound("File")
    inline = not download and asset.mime_type.startswith(INLINE)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.original_name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/health", summary="Liveness and seed status", tags=["health"])
async def health(svc: SvcDep) -> dict:
    return {"status": "ok", "app": svc.settings.app_name, "seed": svc.seed_status}
