from __future__ import annotations

import pytest
import sqlalchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless.db import Database, DatabaseConfig, Session
from takeless.errors import Errors
from takeless.health import Health

URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def database():
    db = Database(DatabaseConfig(url=URL))
    yield db
    await db.shutdown()


async def test_session_context_manager_runs_queries(database: Database):
    async with database.session() as session:
        result = await session.execute(sqlalchemy.text("SELECT 42"))
        assert result.scalar_one() == 42


async def test_session_rolls_back_when_the_block_raises(database: Database):
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            await session.execute(sqlalchemy.text("SELECT 1"))
            raise RuntimeError("boom")
    # The session survived the rollback and is still usable afterwards.
    async with database.session() as session:
        assert (await session.execute(sqlalchemy.text("SELECT 1"))).scalar_one() == 1


async def test_health_check_reports_the_dialect(database: Database):
    check = await database.check()
    assert check is not None
    assert check.healthy
    assert check.meta["dialect"] == "sqlite"


async def test_health_check_can_be_turned_off():
    db = Database(DatabaseConfig(url=URL, health_check=False))
    assert await db.check() is None
    await db.shutdown()


async def test_a_dead_database_reports_unhealthy():
    db = Database(DatabaseConfig(url="sqlite+aiosqlite:////nonexistent/dir/db.sqlite"))
    with pytest.raises(Exception):  # noqa: B017 - the driver's own error type
        await db.check()
    await db.shutdown()


def test_session_dependency_serves_a_request():
    app = FastAPI()
    Errors().setup(app)
    Database(DatabaseConfig(url=URL)).setup(app)

    @app.get("/answer")
    async def answer(session: Session):
        result = await session.execute(sqlalchemy.text("SELECT 42"))
        return {"answer": result.scalar_one()}

    with TestClient(app) as client:
        assert client.get("/answer").json() == {"answer": 42}


def test_database_shows_up_in_health_without_being_listed():
    app = FastAPI()
    Health().setup(app)
    Database(DatabaseConfig(url=URL)).setup(app)

    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["db"]["dialect"] == "sqlite"


def test_setting_up_alone_still_closes_the_engine_on_shutdown():
    """The modular path has to manage its own lifecycle. Without this, a
    `Database(...).setup(app)` with no `Takeless` anywhere leaks its pool — and
    aiosqlite's thread then outlives the loop and raises from nowhere."""
    app = FastAPI()
    database = Database(DatabaseConfig(url=URL))
    database.setup(app)

    disposed = False
    original = database.shutdown

    async def record():
        nonlocal disposed
        disposed = True
        await original()

    database.shutdown = record

    with TestClient(app):
        assert not disposed
    assert disposed


def test_the_lifespan_disposes_the_engine():
    from takeless import Takeless

    takeless = Takeless(db=DatabaseConfig(url=URL), docs=False, health=False)
    database = takeless.db
    assert database is not None

    disposed = False
    original = database.shutdown

    async def record():
        nonlocal disposed
        disposed = True
        await original()

    database.shutdown = record

    app = FastAPI()
    takeless.setup(app)
    with TestClient(app):
        pass
    assert disposed
