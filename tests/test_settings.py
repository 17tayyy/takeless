from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from takeless.settings import (
    BaseAppSettings,
    SecretResolutionError,
    configure_secrets,
)


class Settings(BaseAppSettings):
    app_name: str = "svc"
    jwt_secret: str = "secret://jwt-signing-key"
    plain: str = "not-a-secret"


def test_secret_reference_resolves_from_environment(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", "s3cr3t")
    settings = Settings()
    assert settings.jwt_secret == "s3cr3t"
    assert settings.plain == "not-a-secret"


def test_secret_reference_from_env_var_value_is_also_resolved(monkeypatch):
    """The reference can arrive through the environment, not just as a default."""
    monkeypatch.setenv("PLAIN", "secret://other-key")
    monkeypatch.setenv("OTHER_KEY", "resolved")
    monkeypatch.setenv("JWT_SIGNING_KEY", "x")
    assert Settings().plain == "resolved"


def test_json_key_reference(monkeypatch):
    class DbSettings(BaseAppSettings):
        db_url: str = "secret://prod-db#url"

    monkeypatch.setenv("PROD_DB", json.dumps({"url": "postgresql://host/db", "x": 1}))
    assert DbSettings().db_url == "postgresql://host/db"


def test_missing_secret_fails_at_construction(monkeypatch):
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    with pytest.raises(SecretResolutionError, match="JWT_SIGNING_KEY"):
        Settings()


def test_missing_json_key_is_reported(monkeypatch):
    class DbSettings(BaseAppSettings):
        db_url: str = "secret://prod-db#url"

    monkeypatch.setenv("PROD_DB", json.dumps({"other": 1}))
    with pytest.raises(SecretResolutionError, match="has no key 'url'"):
        DbSettings()


def test_secretstr_fields_are_resolved(monkeypatch):
    class Wrapped(BaseAppSettings):
        token: SecretStr = SecretStr("secret://api-token")

    monkeypatch.setenv("API_TOKEN", "abc")
    assert Wrapped().token.get_secret_value() == "abc"


def test_null_provider_refuses_instead_of_passing_the_uri_through():
    configure_secrets("none")
    with pytest.raises(SecretResolutionError, match="no secrets provider"):
        Settings()


def test_custom_provider_object():
    class Static:
        def get(self, name: str) -> str:
            return f"value-for-{name}"

        def close(self) -> None: ...

    configure_secrets(Static())
    assert Settings().jwt_secret == "value-for-jwt-signing-key"


def test_env_prefix_option(monkeypatch):
    monkeypatch.setenv("APP_JWT_SIGNING_KEY", "prefixed")
    configure_secrets("env", prefix="app_")
    assert Settings().jwt_secret == "prefixed"


@pytest.mark.parametrize(
    ("given", "expected"),
    [("prod", "production"), ("DEV", "development"), ("stage", "staging")],
)
def test_environment_aliases(given, expected, monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", "x")
    assert Settings(environment=given).environment == expected


def test_is_production(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", "x")
    assert Settings(environment="prod").is_production
    assert not Settings(environment="staging").is_production


def test_unknown_provider_name():
    with pytest.raises(SecretResolutionError, match="unknown secrets provider"):
        configure_secrets("gcp")
