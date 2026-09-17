"""Async SQLAlchemy engine for the in-memory SQLite database.

`sqlite+aiosqlite:///:memory:` only lives as long as its connection, so the engine uses a
StaticPool (one shared connection). SQLite transactions are per connection, which means two
sessions using it at once would share one transaction. `Database.session()` therefore hands out
one session at a time; a task that already holds the session gets the same one back, so service
functions can be composed without deadlocking.
"""

import asyncio
import contextvars
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import back_end.db.models  # noqa: F401  (registers the tables on SQLModel.metadata)

_current: contextvars.ContextVar[AsyncSession | None] = contextvars.ContextVar("linemate_session", default=None)


class Database:
    def __init__(self, url: str):
        self.url = url
        in_memory = ":memory:" in url or "mode=memory" in url
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if in_memory:
            kwargs["poolclass"] = StaticPool
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self._factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False, autoflush=True)
        self._lock = asyncio.Lock()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.drop_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        existing = _current.get()
        if existing is not None:
            yield existing
            return
        async with self._lock, self._factory() as session:
            token = _current.set(session)
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
            finally:
                _current.reset(token)

    async def dispose(self) -> None:
        await self.engine.dispose()


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_db: Database | None = None


def init_database(url: str) -> Database:
    global _db
    _db = Database(url)
    return _db


def get_database() -> Database:
    if _db is None:
        raise RuntimeError("Database is not initialised")
    return _db
