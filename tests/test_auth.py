from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from takeless.auth import (
    Auth,
    AuthConfig,
    AuthenticatedUser,
    CurrentUser,
    OptionalUser,
    require_scopes,
)
from takeless.errors import Errors, Unauthorized

#: 32 bytes, the minimum an HS256 key may be.
SECRET = "test-signing-key-0123456789abcdef"


@pytest.fixture
def auth() -> Auth:
    return Auth(
        AuthConfig(
            secret=SECRET, hash_memory_cost=8, hash_time_cost=1, hash_parallelism=1
        )
    )


@pytest.fixture
def client(auth: Auth) -> TestClient:
    app = FastAPI()
    Errors().setup(app)
    auth.setup(app)

    @app.get("/me")
    async def me(user: AuthenticatedUser):
        return {"id": user.id, "email": user.email, "scopes": list(user.scopes)}

    @app.get("/maybe")
    async def maybe(user: OptionalUser):
        return {"id": user.id if user else None}

    @app.delete("/users/{user_id}")
    async def delete_user(
        user_id: str, _: CurrentUser = Depends(require_scopes("users:write"))
    ):
        return {"deleted": user_id}

    return TestClient(app)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- tokens ------------------------------------------------------------------


def test_token_round_trip(auth: Auth):
    token = auth.create_access_token("42", scopes=("read",), email="a@b.c", tenant="x")
    user = auth.user_from_token(token)
    assert user.id == "42"
    assert user.email == "a@b.c"
    assert user.scopes == ("read",)
    assert user.claims["tenant"] == "x"
    assert user.token == token


def test_expired_token_has_its_own_code(auth: Auth):
    token = auth.create_access_token("42", ttl=timedelta(seconds=-1))
    with pytest.raises(Unauthorized) as raised:
        auth.user_from_token(token)
    assert raised.value.code == "token_expired"


def test_token_signed_with_another_key_is_refused(auth: Auth):
    other = Auth(AuthConfig(secret="a-different-key-0123456789abcdefgh"))
    with pytest.raises(Unauthorized) as raised:
        auth.user_from_token(other.create_access_token("42"))
    assert raised.value.code == "invalid_token"


def test_refresh_token_cannot_be_used_as_an_access_token(auth: Auth):
    with pytest.raises(Unauthorized) as raised:
        auth.user_from_token(auth.create_refresh_token("42"))
    assert raised.value.code == "wrong_token_type"


def test_refresh_token_validates_as_a_refresh_token(auth: Auth):
    claims = auth.decode(auth.create_refresh_token("42"), expected_type="refresh")
    assert claims["sub"] == "42"


def test_token_pair_reports_the_access_lifetime():
    auth = Auth(AuthConfig(secret=SECRET, access_token_ttl=timedelta(minutes=5)))
    pair = auth.create_token_pair("42", scopes=("read",))
    assert pair.expires_in == 300
    assert pair.token_type == "bearer"
    assert auth.user_from_token(pair.access_token).id == "42"


def test_audience_and_issuer_are_enforced():
    issuing = Auth(AuthConfig(secret=SECRET, audience="api", issuer="auth-service"))
    token = issuing.create_access_token("42")
    assert issuing.user_from_token(token).id == "42"

    mismatched = Auth(
        AuthConfig(secret=SECRET, audience="other", issuer="auth-service")
    )
    with pytest.raises(Unauthorized):
        mismatched.user_from_token(token)


def test_space_delimited_scopes_are_accepted(auth: Auth):
    """OAuth 2 writes scopes as one string; both spellings must work."""
    token = auth.tokens.issue("42", scopes=[])
    import jwt

    payload = jwt.decode(
        token, SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    payload["scopes"] = "read write"
    reissued = jwt.encode(payload, SECRET, algorithm="HS256")
    assert auth.user_from_token(reissued).scopes == ("read", "write")


def test_hs_algorithm_requires_a_secret():
    with pytest.raises(ValueError, match="secret="):
        AuthConfig(algorithm="HS256")


def test_asymmetric_algorithm_requires_a_key_pair():
    with pytest.raises(ValueError, match="private_key"):
        AuthConfig(algorithm="RS256")


# -- passwords ---------------------------------------------------------------


def test_password_hashing(auth: Auth):
    hashed = auth.hash_password("correct horse")
    assert hashed != "correct horse"
    assert auth.verify_password("correct horse", hashed)
    assert not auth.verify_password("wrong horse", hashed)


def test_verifying_a_malformed_hash_returns_false_rather_than_raising(auth: Auth):
    assert not auth.verify_password("anything", "not-a-hash")


def test_rehash_is_requested_when_parameters_get_stronger():
    weak = Auth(
        AuthConfig(
            secret=SECRET, hash_time_cost=1, hash_memory_cost=8, hash_parallelism=1
        )
    )
    strong = Auth(
        AuthConfig(
            secret=SECRET, hash_time_cost=4, hash_memory_cost=64, hash_parallelism=1
        )
    )
    hashed = weak.hash_password("pw")
    assert strong.password_needs_rehash(hashed)
    assert not weak.password_needs_rehash(hashed)


# -- dependencies ------------------------------------------------------------


def test_require_auth_accepts_a_valid_token(client: TestClient, auth: Auth):
    token = auth.create_access_token("42", scopes=("read",), email="a@b.c")
    response = client.get("/me", headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {"id": "42", "email": "a@b.c", "scopes": ["read"]}


def test_require_auth_without_a_header(client: TestClient):
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"
    assert response.headers["www-authenticate"] == "Bearer"


def test_require_auth_with_a_broken_token(client: TestClient):
    response = client.get("/me", headers=bearer("not.a.token"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_optional_auth_allows_anonymous(client: TestClient):
    assert client.get("/maybe").json() == {"id": None}


def test_optional_auth_still_refuses_a_broken_token(client: TestClient):
    """An invalid token is a bug or an expiry, not anonymity."""
    assert client.get("/maybe", headers=bearer("garbage")).status_code == 401


def test_require_scopes_allows_a_granted_scope(client: TestClient, auth: Auth):
    token = auth.create_access_token("42", scopes=("users:write",))
    assert client.delete("/users/9", headers=bearer(token)).status_code == 200


def test_require_scopes_refuses_a_missing_scope(client: TestClient, auth: Auth):
    token = auth.create_access_token("42", scopes=("users:read",))
    response = client.delete("/users/9", headers=bearer(token))
    assert response.status_code == 403
    body = response.json()["error"]
    assert body["code"] == "insufficient_scope"
    assert body["details"] == {"required": ["users:write"], "granted": ["users:read"]}


def test_dependency_without_the_module_set_up_explains_itself():
    app = FastAPI()
    Errors().setup(app)

    @app.get("/me")
    async def me(user: AuthenticatedUser):
        return {"id": user.id}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/me", headers=bearer("whatever"))
    assert response.status_code == 500


def test_authentication_binds_the_user_onto_the_log_context(
    client: TestClient, auth: Auth
):
    from takeless.observability import get_context

    captured: dict[str, object] = {}

    @client.app.get("/probe")
    async def probe(user: AuthenticatedUser):
        captured.update(get_context())
        return {}

    client.get("/probe", headers=bearer(auth.create_access_token("42")))
    assert captured["user_id"] == "42"


def test_bearer_scheme_reaches_the_openapi_document(client: TestClient):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert "bearerAuth" in schemes
