"""Async SQLAlchemy: one engine, one session factory, one dependency.

Requires `pip install 'takeless[db]'`.

Standalone:

    from takeless.db import Database, DatabaseConfig, Session

    db = Database(DatabaseConfig(url="postgresql+asyncpg://..."))
    db.setup(app)

    @app.get("/users")
    async def list_users(session: Session):
        return (await session.execute(select(User))).scalars().all()

Outside a request — a CLI, a test, a startup migration — use the context
manager instead: `async with db.session() as session: ...`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from takeless.core.component import Check, Component, get_component
from takeless.core.deps import require_dependency

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

_sa_asyncio = require_dependency("sqlalchemy.ext.asyncio")
AsyncSession = _sa_asyncio.AsyncSession

__all__ = [
    "AsyncSession",
    "Database",
    "DatabaseConfig",
    "Session",
    "get_session",
]


class DatabaseConfig(BaseModel):
    """Engine, pool and session settings."""

    model_config = ConfigDict(extra="forbid")

    #: An async driver URL: `postgresql+asyncpg://`, `sqlite+aiosqlite://`, ...
    url: str

    echo: bool = False

    pool_size: int = 5
    max_overflow: int = 10
    #: Recycle connections before a proxy or the server silently drops them.
    #: 1800s sits under the common 3600s idle timeouts.
    pool_recycle: int = 1800
    #: Costs one round trip per checkout and removes the entire class of
    #: "server closed the connection unexpectedly" errors after a failover.
    pool_pre_ping: bool = True
    pool_timeout: float = 30.0

    connect_args: dict[str, Any] = Field(default_factory=dict)

    #: Off, so attributes stay readable after commit — otherwise every field
    #: access after committing triggers a lazy reload, and in async that raises
    #: rather than quietly working.
    expire_on_commit: bool = False
    autoflush: bool = True

    #: Commit the request's session when the endpoint returns without raising.
    #: Off by default: a commit should be a thing you wrote, not a side effect
    #: of returning a value.
    commit_on_exit: bool = False

    #: Run `SELECT 1` in `/health`.
    health_check: bool = True


class Database(Component):
    """Owns the engine and the session factory."""

    name = "db"

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        options: dict[str, Any] = {
            "echo": config.echo,
            "pool_pre_ping": config.pool_pre_ping,
            "connect_args": config.connect_args,
        }

        if not config.url.startswith("sqlite"):
            options |= {
                "pool_size": config.pool_size,
                "max_overflow": config.max_overflow,
                "pool_recycle": config.pool_recycle,
                "pool_timeout": config.pool_timeout,
            }

        self.engine: AsyncEngine = _sa_asyncio.create_async_engine(
            config.url, **options
        )
        self.session_factory: async_sessionmaker[AsyncSession] = (
            _sa_asyncio.async_sessionmaker(
                bind=self.engine,
                expire_on_commit=config.expire_on_commit,
                autoflush=config.autoflush,
            )
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session scoped to this block, rolled back if the block raises."""
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def get_session(self) -> AsyncIterator[AsyncSession]:
        """The FastAPI dependency form: one session per request."""
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            if self.config.commit_on_exit:
                await session.commit()

    async def shutdown(self) -> None:
        await self.engine.dispose()

    async def check(self) -> Check | None:
        if not self.config.health_check:
            return None
        sqlalchemy = require_dependency("sqlalchemy")
        async with self.engine.connect() as connection:
            await connection.execute(sqlalchemy.text("SELECT 1"))
        return Check(
            name=self.name,
            healthy=True,
            meta={"dialect": self.engine.dialect.name},
        )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Importable dependency yielding a request-scoped session."""
    database = get_component(request.app, Database)
    async for session in database.get_session():
        yield session


#: `async def handler(session: Session): ...`
Session = Annotated[AsyncSession, Depends(get_session)]
