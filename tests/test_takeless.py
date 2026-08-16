"""The fast path, and its equivalence to the modular one."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from takeless import Takeless
from takeless.auth import Auth, AuthConfig, AuthenticatedUser
from takeless.core.component import Check, Component
from takeless.observability import ObservabilityConfig
from takeless.settings import BaseAppSettings

DB_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "takeless-test-signing-key-0123456789"


class Settings(BaseAppSettings):
    app_name: str = "svc"


# -- defaults ----------------------------------------------------------------


def test_the_four_defaults_are_on_without_being_asked_for():
    takeless = Takeless()
    assert set(takeless.components) == {"observability", "errors", "health", "docs"}


def test_a_default_module_can_be_turned_off():
    takeless = Takeless(docs=False, health=False)
    assert set(takeless.components) == {"observability", "errors"}


def test_optional_modules_stay_off_until_configured():
    takeless = Takeless()
    assert takeless.auth is None
    assert takeless.db is None
    assert takeless.jobs is None
    assert takeless.rate_limit is None
    assert takeless.cors is None


def test_config_accepts_a_dict_an_object_or_true():
    from_dict = Takeless(logging={"level": "DEBUG"})
    from_object = Takeless(logging=ObservabilityConfig(level="DEBUG"))
    from_true = Takeless(logging=True)
    assert from_dict.observability.config.level == "DEBUG"
    assert from_object.observability.config.level == "DEBUG"
    assert from_true.observability.config.level == "INFO"


def test_a_bad_config_type_is_refused():
    with pytest.raises(TypeError, match="expected a dict"):
        Takeless(logging=42)


def test_unknown_config_keys_are_refused():
    with pytest.raises(ValueError, match="typo"):
        Takeless(logging={"typo": True})


# -- settings feed the modules -----------------------------------------------


def test_settings_supply_the_environment_and_the_service_name():
    takeless = Takeless(settings=Settings(environment="production"))
    assert takeless.docs.config.environment == "production"
    assert takeless.observability.config.service == "svc"
    # JSON logs everywhere but development, without being asked.
    assert takeless.observability.config.json_logs is True


def test_development_gets_readable_logs():
    takeless = Takeless(settings=Settings(environment="development"))
    assert takeless.observability.config.json_logs is False


def test_an_explicit_choice_beats_the_environment():
    takeless = Takeless(
        settings=Settings(environment="production"), logging={"json_logs": False}
    )
    assert takeless.observability.config.json_logs is False


def test_docs_disappear_in_production_without_being_configured():
    takeless = Takeless(settings=Settings(environment="production"))
    app = FastAPI()
    takeless.setup(app)
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# -- wiring ------------------------------------------------------------------


def test_setup_wires_every_module_into_one_app():
    takeless = Takeless(
        settings=Settings(),
        auth={"secret": SECRET},
        db={"url": DB_URL},
        cors={"allow_origins": ["https://myapp.com"]},
        rate_limit={"default_limit": "100/minute"},
    )
    app = FastAPI()
    takeless.setup(app)

    @app.get("/me")
    async def me(user: AuthenticatedUser):
        return {"id": user.id}

    with TestClient(app) as client:
        token = takeless.auth.create_access_token("42")
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.json() == {"id": "42"}
    assert response.headers["x-request-id"]
    assert response.headers["x-ratelimit-limit"] == "100"


def test_setting_up_twice_on_one_app_is_refused():
    takeless = Takeless()
    app = FastAPI()
    takeless.setup(app)
    with pytest.raises(RuntimeError, match="already called"):
        takeless.setup(app)


def test_errors_from_any_module_share_the_envelope():
    takeless = Takeless(settings=Settings(), auth={"secret": SECRET})
    app = FastAPI()
    takeless.setup(app)

    @app.get("/me")
    async def me(user: AuthenticatedUser):
        return {}

    with TestClient(app) as client:
        body = client.get("/me").json()
    assert body["error"]["code"] == "missing_token"
    assert body["error"]["request_id"]


def test_rate_limit_refusals_still_carry_a_request_id_and_cors():
    """The middleware order is what makes this true: CORS outermost, then the
    request context, then the limiter."""
    takeless = Takeless(
        cors={"allow_origins": ["https://myapp.com"]},
        rate_limit={"default_limit": "1/minute"},
    )
    app = FastAPI()
    takeless.setup(app)

    @app.get("/ping")
    async def ping():
        return {}

    headers = {"Origin": "https://myapp.com"}
    with TestClient(app) as client:
        client.get("/ping", headers=headers)
        refused = client.get("/ping", headers=headers)

    assert refused.status_code == 429
    assert refused.headers["x-request-id"]
    assert refused.headers["access-control-allow-origin"] == "https://myapp.com"
    assert refused.json()["error"]["request_id"] == refused.headers["x-request-id"]


def test_health_aggregates_whatever_was_configured():
    takeless = Takeless(settings=Settings(), db={"url": DB_URL})
    app = FastAPI()
    takeless.setup(app)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"db"}


# -- lifespan ----------------------------------------------------------------


class Recorder(Component):
    name = "db"  # borrows a slot in the setup order

    def __init__(self) -> None:
        self.events: list[str] = []

    async def startup(self) -> None:
        self.events.append("startup")

    async def shutdown(self) -> None:
        self.events.append("shutdown")

    async def check(self) -> Check:
        return Check(name=self.name, healthy=True)


def test_components_start_and_stop_with_the_app():
    takeless = Takeless(docs=False)
    recorder = Recorder()
    takeless._components["db"] = recorder

    app = FastAPI()
    takeless.setup(app)
    with TestClient(app):
        assert recorder.events == ["startup"]
    assert recorder.events == ["startup", "shutdown"]


def test_a_user_supplied_lifespan_survives_and_runs_inside_ours():
    order: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        order.append("user in")
        yield
        order.append("user out")

    takeless = Takeless(docs=False)
    recorder = Recorder()
    recorder.events = order  # share one log to see the interleaving
    takeless._components["db"] = recorder

    app = FastAPI(lifespan=lifespan)
    takeless.setup(app)
    with TestClient(app):
        pass

    assert order == ["startup", "user in", "user out", "shutdown"]


class FailingStart(Component):
    name = "jobs"

    async def startup(self) -> None:
        raise RuntimeError("cannot reach the broker")


def test_a_failed_startup_rolls_back_what_already_started():
    takeless = Takeless(docs=False, health=False)
    recorder = Recorder()
    takeless._components["db"] = recorder
    takeless._components["jobs"] = FailingStart()

    app = FastAPI()
    takeless.setup(app)
    with pytest.raises(RuntimeError, match="cannot reach the broker"), TestClient(app):
        pass
    assert recorder.events == ["startup", "shutdown"]


# -- equivalence with the modular path ---------------------------------------


def test_the_two_paths_produce_the_same_behaviour():
    """`Takeless` is sugar: the module built by hand and the one it builds must
    be indistinguishable from a request's point of view."""

    def routes(app: FastAPI) -> None:
        @app.get("/me")
        async def me(user: AuthenticatedUser):
            return {"id": user.id}

    central = Takeless(auth={"secret": SECRET}, docs=False, health=False)
    central_app = FastAPI()
    central.setup(central_app)
    routes(central_app)

    manual_app = FastAPI()
    from takeless.errors import Errors
    from takeless.observability import Observability

    Observability().setup(manual_app)
    Errors().setup(manual_app)
    manual_auth = Auth(AuthConfig(secret=SECRET))
    manual_auth.setup(manual_app)
    routes(manual_app)

    token = manual_auth.create_access_token("42")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(central_app) as a, TestClient(manual_app) as b:
        assert (
            a.get("/me", headers=headers).json() == b.get("/me", headers=headers).json()
        )
        assert (
            a.get("/me").json()["error"]["code"] == b.get("/me").json()["error"]["code"]
        )


def test_the_logger_works_before_any_app_exists():
    takeless = Takeless(settings=Settings())
    takeless.logger.info("built")  # must not raise


async def test_missing_module_dependencies_are_reported_at_use_time():
    takeless = Takeless()
    with pytest.raises(RuntimeError, match="jobs is not configured"):
        takeless.get_jobs_client()
    with pytest.raises(RuntimeError, match="db is not configured"):
        await anext(aiter(takeless.get_session()))


def test_the_takeless_bound_dependencies_work_as_documented():
    """`Depends(takeless.get_session)` is the spelling in the brief, so it has
    to be something FastAPI recognises as a yielding dependency rather than a
    function that happens to return a generator."""
    import sqlalchemy
    from fastapi import Depends

    takeless = Takeless(db={"url": DB_URL}, docs=False)
    app = FastAPI()
    takeless.setup(app)

    @app.get("/answer")
    async def answer(session=Depends(takeless.get_session)):
        result = await session.execute(sqlalchemy.text("SELECT 42"))
        return {"answer": result.scalar_one()}

    with TestClient(app) as client:
        assert client.get("/answer").json() == {"answer": 42}


def test_secrets_can_be_configured_through_takeless(monkeypatch):
    from takeless.settings import get_secrets_provider, resolve_secret

    monkeypatch.setenv("APP_TOKEN", "resolved")
    takeless = Takeless(secrets={"provider": "env", "prefix": "app_"}, docs=False)
    assert takeless.secrets is not None
    assert get_secrets_provider() is takeless.secrets.provider
    assert resolve_secret("secret://token") == "resolved"
