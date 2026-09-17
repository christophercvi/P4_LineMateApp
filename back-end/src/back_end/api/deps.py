"""FastAPI dependencies: the service container and the signed-in actor.

Authentication: the client sends `Authorization: Bearer <JWT>`. The JWT (PyJWT, HS256) is issued by
/api/auth/login after the password is verified against its argon2 hash. On every request the token is
decoded, the user is re-read from the database (so a disabled account or a changed role takes effect
immediately) and turned into an `Actor` that the permission rules understand.
"""

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from back_end.auth.permissions import Actor
from back_end.core.errors import Forbidden, Unauthorized
from back_end.core.security import decode_access_token
from back_end.db import models as m
from back_end.services import repo
from back_end.services.container import Services, get_services

_bearer = HTTPBearer(auto_error=False, description="JWT from POST /api/auth/login")


def services() -> Services:
    return get_services()


SvcDep = Annotated[Services, Depends(services)]


async def current_actor(
    svc: SvcDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    if creds is None or not creds.credentials:
        raise Unauthorized("Sign in to continue.")
    try:
        claims = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Your session has expired. Sign in again.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token.") from exc
    async with svc.db.session() as s:
        user = await s.get(m.User, claims.get("sub", ""))
        if user is None or not user.active:
            raise Unauthorized("This account is disabled or no longer exists.")
        if user.role == "service":
            raise Forbidden("Service accounts can only use the MCP endpoint.")
        return repo.actor_for(user)


ActorDep = Annotated[Actor, Depends(current_actor)]


def require(predicate: Callable[[Actor], bool], message: str) -> Callable[..., object]:
    """Route-level guard: `dependencies=[Depends(require(can.admin, "..."))]`."""

    async def guard(actor: ActorDep) -> Actor:
        if not predicate(actor):
            raise Forbidden(message)
        return actor

    return guard
