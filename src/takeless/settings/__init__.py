"""Settings, with `secret://` references resolved against a secrets manager.

    from takeless.settings import BaseAppSettings

    class Settings(BaseAppSettings):
        app_name: str = "my-service"
        jwt_secret: str = "secret://jwt-signing-key"
        db_url: str = "secret://prod-db#url"

Everything pydantic-settings does still applies — env vars, `.env` files,
nested models. The only addition is that a resolved value starting with
`secret://` is fetched from the provider instead of being taken literally.

Ordering: the provider is chosen before the first `Settings()` is built, either
by `TAKELESS_SECRETS_PROVIDER` (the usual production path) or by calling
`configure_secrets()` at the top of the module. `Takeless(secrets=...)` also
calls it, but only affects settings instantiated afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from takeless.core.component import Component
from takeless.settings.secrets import (
    EnvSecretsProvider,
    NullSecretsProvider,
    SecretRef,
    SecretResolutionError,
    SecretsProvider,
    configure_secrets,
    get_secrets_provider,
    is_secret_ref,
    reset_secrets,
    resolve_secret,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "BaseAppSettings",
    "EnvSecretsProvider",
    "NullSecretsProvider",
    "SecretRef",
    "SecretResolutionError",
    "Secrets",
    "SecretsConfig",
    "SecretsProvider",
    "configure_secrets",
    "get_secrets_provider",
    "reset_secrets",
    "resolve_secret",
]

#: Short forms people actually type, mapped to the canonical names the rest of
#: the library branches on.
_ENVIRONMENT_ALIASES = {
    "dev": "development",
    "local": "development",
    "stage": "staging",
    "stg": "staging",
    "prod": "production",
    "prd": "production",
    "testing": "test",
}


class BaseAppSettings(BaseSettings):
    """Base class for a service's settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "app"
    environment: str = "development"
    debug: bool = False

    @field_validator("environment", mode="before")
    @classmethod
    def _canonical_environment(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        lowered = value.strip().lower()
        return _ENVIRONMENT_ALIASES.get(lowered, lowered)

    @model_validator(mode="after")
    def _resolve_secret_references(self) -> BaseAppSettings:
        """Swap every `secret://...` value for the secret it points at.

        Runs after normal resolution, so it covers both a `secret://` default
        written in the class and one injected through an env var.
        """
        _resolve_in_place(self)
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


def _resolve_in_place(model: BaseModel) -> None:
    """Resolve secret references on `model` and any nested models."""
    for name in type(model).model_fields:
        value = getattr(model, name, None)
        replacement = _resolved_value(value)
        if replacement is not _UNCHANGED:
            object.__setattr__(model, name, replacement)
        elif isinstance(value, BaseModel):
            _resolve_in_place(value)


class _Unchanged:
    __slots__ = ()


_UNCHANGED = _Unchanged()


def _resolved_value(value: Any) -> Any:
    """The resolved form of `value`, or `_UNCHANGED` if it holds no reference."""
    if is_secret_ref(value):
        return resolve_secret(value)
    if isinstance(value, SecretStr) and is_secret_ref(value.get_secret_value()):
        return SecretStr(resolve_secret(value.get_secret_value()))
    if isinstance(value, list) and any(is_secret_ref(item) for item in value):
        return [resolve_secret(i) if is_secret_ref(i) else i for i in value]
    if isinstance(value, dict) and any(is_secret_ref(v) for v in value.values()):
        return {
            k: resolve_secret(v) if is_secret_ref(v) else v for k, v in value.items()
        }
    return _UNCHANGED


class SecretsConfig(BaseModel):
    """Which secrets provider to use. Extra keys are passed to the provider,
    so `{"provider": "aws", "region": "eu-west-1"}` works as written."""

    model_config = ConfigDict(extra="allow")

    provider: str = "env"

    @property
    def options(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Secrets(Component):
    """Selects the process-wide secrets provider.

    Configuring happens in `__init__`, not `setup()`, because settings are
    normally built before any FastAPI app exists.
    """

    name = "secrets"

    def __init__(self, config: SecretsConfig | None = None) -> None:
        self.config = config or SecretsConfig()
        self.provider = configure_secrets(self.config.provider, **self.config.options)

    def setup(self, app: FastAPI) -> None:
        super().setup(app)

    async def shutdown(self) -> None:
        close = getattr(self.provider, "close", None)
        if close is not None:
            close()
