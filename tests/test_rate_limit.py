from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from takeless.errors import Errors
from takeless.rate_limit import (
    MemoryBackend,
    RateLimit,
    RateLimitConfig,
    RateLimitConfigError,
    RateLimiter,
    Rule,
)

# -- parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "limit", "window"),
    [
        ("100/minute", 100, 60),
        ("10/second", 10, 1),
        ("1000/hour", 1000, 3600),
        ("5/day", 5, 86400),
        ("30/15minutes", 30, 900),
        (" 20 / 2 h ", 20, 7200),
    ],
)
def test_rule_parsing(expression, limit, window):
    rule = Rule.parse(expression)
    assert (rule.limit, rule.window_seconds) == (limit, window)


@pytest.mark.parametrize(
    "expression", ["100", "100/fortnight", "abc/minute", "0/minute"]
)
def test_bad_rules_are_refused(expression):
    with pytest.raises(RateLimitConfigError):
        Rule.parse(expression)


def test_bad_default_limit_fails_at_config_time():
    with pytest.raises(ValueError, match="cannot parse rate limit"):
        RateLimitConfig(default_limit="100 per minute")


def test_redis_backend_needs_a_url():
    with pytest.raises(ValueError, match="needs url="):
        RateLimitConfig(backend="redis")


# -- backend -----------------------------------------------------------------


async def test_memory_backend_counts_down_and_refuses():
    backend = MemoryBackend()
    rule = Rule.parse("3/minute")

    verdicts = [await backend.hit("k", rule) for _ in range(4)]
    assert [v.allowed for v in verdicts] == [True, True, True, False]
    assert [v.remaining for v in verdicts] == [2, 1, 0, -1]
    assert verdicts[0].reset_after == pytest.approx(60, abs=1)


async def test_memory_backend_keys_are_independent():
    backend = MemoryBackend()
    rule = Rule.parse("1/minute")
    assert (await backend.hit("a", rule)).allowed
    assert (await backend.hit("b", rule)).allowed
    assert not (await backend.hit("a", rule)).allowed


async def test_reset_clears_a_counter():
    backend = MemoryBackend()
    rule = Rule.parse("1/minute")
    await backend.hit("a", rule)
    await backend.reset("a")
    assert (await backend.hit("a", rule)).allowed


# -- integration -------------------------------------------------------------


def build_app(config: RateLimitConfig) -> FastAPI:
    app = FastAPI()
    Errors().setup(app)
    limiter = RateLimiter(config)
    limiter.setup(app)

    @app.get("/open")
    async def open_route():
        return {"ok": True}

    @app.post("/login", dependencies=[Depends(RateLimit("2/minute"))])
    async def login():
        return {"ok": True}

    @app.post(
        "/reset-password", dependencies=[Depends(RateLimit("2/minute", scope="email"))]
    )
    async def reset_password():
        return {"ok": True}

    @app.post("/invite", dependencies=[Depends(RateLimit("2/minute", scope="email"))])
    async def invite():
        return {"ok": True}

    return app


def test_global_limit_refuses_over_the_line():
    client = TestClient(build_app(RateLimitConfig(default_limit="2/minute")))
    assert client.get("/open").status_code == 200
    assert client.get("/open").status_code == 200

    refused = client.get("/open")
    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["code"] == "too_many_requests"
    assert refused.headers["retry-after"]
    assert refused.headers["x-ratelimit-limit"] == "2"
    assert refused.headers["x-ratelimit-remaining"] == "0"


def test_allowed_responses_carry_the_headers():
    client = TestClient(build_app(RateLimitConfig(default_limit="5/minute")))
    response = client.get("/open")
    assert response.headers["x-ratelimit-limit"] == "5"
    assert response.headers["x-ratelimit-remaining"] == "4"


def test_headers_can_be_suppressed():
    config = RateLimitConfig(default_limit="5/minute", send_headers=False)
    response = TestClient(build_app(config)).get("/open")
    assert "x-ratelimit-limit" not in response.headers


def test_paths_get_separate_buckets():
    client = TestClient(build_app(RateLimitConfig(default_limit="2/minute")))
    assert client.get("/open").status_code == 200
    assert client.get("/open").status_code == 200
    assert client.post("/login").status_code == 200


def test_one_bucket_for_everything_when_per_path_is_off():
    config = RateLimitConfig(default_limit="2/minute", per_path=False)
    client = TestClient(build_app(config))
    assert client.get("/open").status_code == 200
    assert client.post("/login").status_code == 200
    assert client.get("/open").status_code == 429


def test_excluded_paths_are_never_counted():
    config = RateLimitConfig(default_limit="1/minute", exclude_paths=("/open",))
    client = TestClient(build_app(config))
    for _ in range(5):
        assert client.get("/open").status_code == 200


def test_per_route_limit_applies_without_a_global_one():
    client = TestClient(build_app(RateLimitConfig()))
    assert client.post("/login").status_code == 200
    assert client.post("/login").status_code == 200
    assert client.post("/login").status_code == 429


def test_a_shared_scope_pools_the_allowance():
    client = TestClient(build_app(RateLimitConfig()))
    assert client.post("/reset-password").status_code == 200
    assert client.post("/invite").status_code == 200
    assert client.post("/reset-password").status_code == 429


def test_per_route_headers_reach_the_response():
    config = RateLimitConfig(default_limit="100/minute")
    response = TestClient(build_app(config)).post("/login")
    # The route's own 2/minute is the number the client should see, not the
    # global 100/minute the middleware also counted.
    assert response.headers["x-ratelimit-limit"] == "2"


def test_a_custom_key_function_replaces_the_client_address():
    seen: list[str] = []

    def by_api_key(request) -> str:
        key = request.headers.get("x-api-key", "anonymous")
        seen.append(key)
        return key

    config = RateLimitConfig(default_limit="1/minute", key_func=by_api_key)
    client = TestClient(build_app(config))
    assert client.get("/open", headers={"X-API-Key": "one"}).status_code == 200
    assert client.get("/open", headers={"X-API-Key": "two"}).status_code == 200
    assert client.get("/open", headers={"X-API-Key": "one"}).status_code == 429
    assert seen == ["one", "two", "one"]


def test_forwarded_addresses_are_ignored_unless_trusted():
    config = RateLimitConfig(default_limit="1/minute")
    client = TestClient(build_app(config))
    assert (
        client.get("/open", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    )
    assert (
        client.get("/open", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
    )


def test_forwarded_addresses_are_used_when_trusted():
    config = RateLimitConfig(default_limit="1/minute", trust_forwarded=True)
    client = TestClient(build_app(config))
    assert (
        client.get("/open", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    )
    assert (
        client.get("/open", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    )
    assert (
        client.get("/open", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    )
