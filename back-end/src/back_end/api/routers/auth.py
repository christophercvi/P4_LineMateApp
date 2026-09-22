"""Sign in, create an account, and the current user.

Passwords are hashed with argon2id (argon2-cffi). A successful sign-in returns a short-lived JWT
(PyJWT, HS256) that the front end sends as a Bearer token.
"""

from fastapi import APIRouter, status
from sqlmodel import func, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.core.errors import Conflict, Forbidden, Unauthorized
from back_end.core.security import create_access_token, hash_password, needs_rehash, verify_password
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.schemas import CrewOut, LoginIn, LookupsOut, RegisterIn, StationOut, TokenOut, UserOut
from back_end.services import repo

router = APIRouter(prefix="/api", tags=["auth"])


def user_out(u: m.User) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        role=u.role,
        station=u.station_id,
        crew_member_id=u.crew_member_id,
        email=u.email,
        active=u.active,
        last_login=u.last_login,
    )


def _token(svc, u: m.User) -> TokenOut:
    minutes = svc.settings.access_token_minutes
    token = create_access_token(
        {"sub": u.id, "username": u.username, "name": u.display_name, "role": u.role, "station": u.station_id, "crew": u.crew_member_id},
        minutes=minutes,
    )
    return TokenOut(token=token, expires_in=minutes * 60, user=user_out(u))


@router.post("/auth/login", response_model=TokenOut, summary="Sign in with username and password")
async def login(body: LoginIn, svc: SvcDep) -> TokenOut:
    username = body.username.strip().lower()
    async with svc.db.session() as s:
        user = (await s.exec(select(m.User).where(func.lower(m.User.username) == username))).first()
        if user is None or not verify_password(body.password, user.password_hash):
            raise Unauthorized("Wrong username or password.")
        if not user.active:
            raise Forbidden("This account is disabled. Ask an admin to re-enable it.")
        if user.role == "service":
            raise Forbidden("Service accounts use MCP tokens, not interactive sign-in.")
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)
        user.last_login = utcnow()
        s.add(user)
        return _token(svc, user)


@router.post(
    "/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED, summary="Create a Line Cook account and sign in"
)
async def register(body: RegisterIn, svc: SvcDep) -> TokenOut:
    """New accounts join as a Line Cook at the chosen station. A Kitchen Manager or Admin can change the
    role later (Admin > Users)."""
    username = body.username.strip().lower()
    email = str(body.email).strip().lower()
    async with svc.db.session() as s:
        if (await s.exec(select(m.User).where(func.lower(m.User.username) == username))).first():
            raise Conflict("That username is taken.")
        if (await s.exec(select(m.User).where(func.lower(m.User.email) == email))).first():
            raise Conflict("An account with that email already exists.")
        crew_id = await repo.next_id(s, "crew", "CM-", 2)
        name = body.display_name.strip()
        initials = "".join(p[0] for p in name.split()[:2]).upper() or name[:2].upper()
        s.add(m.CrewMember(id=crew_id, name=name, station_id=body.station, title="Line Cook", initials=initials))
        await s.flush()
        user = m.User(
            id=f"u-{username}",
            username=username,
            display_name=name,
            email=email,
            role="line_cook",
            station_id=body.station,
            crew_member_id=crew_id,
            password_hash=hash_password(body.password),
            active=True,
            last_login=utcnow(),
        )
        s.add(user)
        await s.flush()
        return _token(svc, user)


@router.get("/auth/me", response_model=UserOut, summary="The signed-in user")
async def me(actor: ActorDep, svc: SvcDep) -> UserOut:
    async with svc.db.session() as s:
        return user_out(await s.get(m.User, actor.user_id))


@router.get("/lookups", response_model=LookupsOut, summary="Stations and crew (for names, tags and pickers)")
async def lookups(_: ActorDep, svc: SvcDep) -> LookupsOut:
    async with svc.db.session() as s:
        stations = await repo.all_rows(s, m.Station)
        crew = await repo.all_rows(s, m.CrewMember)
    return LookupsOut(
        stations=[StationOut(id=x.id, name=x.name, color=x.color, short=x.short) for x in stations],
        crew=[CrewOut(id=c.id, name=c.name, station=c.station_id, title=c.title, initials=c.initials) for c in crew],
    )


@router.get("/auth/stations", response_model=list[StationOut], summary="Stations a new account can join (no sign-in needed)")
async def public_stations(svc: SvcDep) -> list[StationOut]:
    async with svc.db.session() as s:
        stations = await repo.all_rows(s, m.Station)
    return [StationOut(id=x.id, name=x.name, color=x.color, short=x.short) for x in stations]
