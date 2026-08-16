"""`secret://` reference resolution.

A settings field whose value is `secret://name` is not read from the
environment — it is fetched from the configured secrets provider at
instantiation, and the field ends up holding the real value.

Resolution is synchronous on purpose: settings are built at import time, before
there is an event loop to await anything on.

The provider is process-wide because it is an environment concern, not a code
one: the same image runs against a local `.env` in development and against
Secrets Manager in production, chosen by `TAKELESS_SECRETS_PROVIDER` rather
than by an edit. `configure_secrets()` is the programmatic equivalent, and must
run before the first `Settings()` that needs it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from takeless.errors.exceptions import TakelessError

SECRET_SCHEME = "secret://"


class SecretResolutionError(TakelessError):
    """A `secret://` reference could not be resolved."""


@dataclass(slots=True, frozen=True)
class SecretRef:
    """A parsed `secret://name#json_key` reference."""

    name: str
    key: str | None = None

    @classmethod
    def parse(cls, value: str) -> SecretRef:
        body = value[len(SECRET_SCHEME) :]
        name, _, key = body.partition("#")
        if not name:
            raise SecretResolutionError(
                f"{value!r} has no secret name; expected 'secret://some-name'"
            )
        return cls(name=name, key=key or None)

    def extract(self, payload: str) -> str:
        """Pull this ref's value out of what the provider returned.

        Secrets Manager entries are very often a JSON blob holding a whole
        connection's worth of fields, so `#key` indexes into it rather than
        forcing one secret per value.
        """
        if self.key is None:
            return payload
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SecretResolutionError(
                f"secret {self.name!r} is not JSON, so it has no {self.key!r} key"
            ) from exc
        if not isinstance(document, dict) or self.key not in document:
            raise SecretResolutionError(f"secret {self.name!r} has no key {self.key!r}")
        return str(document[self.key])


@runtime_checkable
class SecretsProvider(Protocol):
    """Where secrets come from. Implement this to add a cloud we don't ship."""

    def get(self, name: str) -> str:
        """The raw stored value of `name`, or raise `SecretResolutionError`."""
        ...

    def close(self) -> None:
        """Release any client held open. Optional."""
        ...


class EnvSecretsProvider:
    """Reads secrets from environment variables. The default.

    `secret://jwt-signing-key` becomes `$JWT_SIGNING_KEY`. It means a service
    written against a secrets manager still runs locally off a `.env` with no
    code change and no cloud credentials.
    """

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix

    def env_name(self, name: str) -> str:
        normalised = name.replace("-", "_").replace("/", "_").replace(".", "_")
        return f"{self.prefix}{normalised}".upper()

    def get(self, name: str) -> str:
        variable = self.env_name(name)
        value = os.environ.get(variable)
        if value is None:
            raise SecretResolutionError(
                f"secret {name!r} resolves to the environment variable "
                f"{variable!r}, which is not set. Set it, or point takeless at a "
                f"secrets manager with TAKELESS_SECRETS_PROVIDER."
            )
        return value

    def close(self) -> None:
        return None


class NullSecretsProvider:
    """Refuses every reference, with an explanation. Used when the configured
    provider name is `none`, so that an unresolved `secret://` fails loudly
    instead of reaching a database driver as a literal string."""

    def get(self, name: str) -> str:
        raise SecretResolutionError(
            f"cannot resolve secret {name!r}: no secrets provider is configured. "
            f"Call takeless.settings.configure_secrets(provider='env'|'aws', ...) "
            f"or set TAKELESS_SECRETS_PROVIDER."
        )

    def close(self) -> None:
        return None


_provider: SecretsProvider | None = None
_cache: dict[str, str] = {}


def configure_secrets(
    provider: str | SecretsProvider = "env", **options: Any
) -> SecretsProvider:
    """Choose the process-wide secrets provider and drop the resolution cache.

    Call before the first `Settings()` that uses `secret://`. Accepts a name
    (`"env"`, `"aws"`, `"none"`) with that provider's options, or a ready-made
    provider object.
    """
    global _provider
    _cache.clear()
    _provider = provider if not isinstance(provider, str) else _build(provider, options)
    return _provider


def get_secrets_provider() -> SecretsProvider:
    """The active provider, built from the environment on first use."""
    global _provider
    if _provider is None:
        _provider = _build(
            os.environ.get("TAKELESS_SECRETS_PROVIDER", "env"),
            _options_from_env(),
        )
    return _provider


def reset_secrets() -> None:
    """Forget the provider and the cache. Mostly for tests."""
    global _provider
    _provider = None
    _cache.clear()


def is_secret_ref(value: object) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_SCHEME)


def resolve_secret(value: str) -> str:
    """Resolve one `secret://` reference, caching per reference."""
    if value in _cache:
        return _cache[value]
    ref = SecretRef.parse(value)
    resolved = ref.extract(get_secrets_provider().get(ref.name))
    _cache[value] = resolved
    return resolved


def _build(name: str, options: dict[str, Any]) -> SecretsProvider:
    match name.lower():
        case "env" | "environment":
            return EnvSecretsProvider(**options)
        case "aws" | "aws-secrets-manager":
            from takeless.settings.aws import AwsSecretsProvider

            return AwsSecretsProvider(**options)
        case "none" | "null":
            return NullSecretsProvider()
        case _:
            raise SecretResolutionError(
                f"unknown secrets provider {name!r}; expected 'env', 'aws' or "
                f"'none', or pass a SecretsProvider instance"
            )


def _options_from_env() -> dict[str, Any]:
    """`TAKELESS_SECRETS_REGION` → `region="..."`, and so on."""
    prefix = "TAKELESS_SECRETS_"
    skip = f"{prefix}PROVIDER"
    return {
        key[len(prefix) :].lower(): value
        for key, value in os.environ.items()
        if key.startswith(prefix) and key != skip
    }
