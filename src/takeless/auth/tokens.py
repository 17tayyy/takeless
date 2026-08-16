"""JWT issuing and validation.

Every failure path lands on `Unauthorized` with a distinct `code`, so a client
can tell "your token expired, refresh it" from "your token is not valid, log in
again" without parsing prose.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from takeless.core.deps import require_dependency
from takeless.errors.exceptions import Unauthorized

if TYPE_CHECKING:
    from takeless.auth.config import AuthConfig

jwt = require_dependency("jwt")

ACCESS = "access"
REFRESH = "refresh"


class TokenIssuer:
    """Signs and verifies tokens for one `AuthConfig`."""

    def __init__(self, config: AuthConfig) -> None:
        self.config = config

    def issue(
        self,
        subject: str,
        *,
        token_type: str = ACCESS,
        ttl: timedelta | None = None,
        scopes: tuple[str, ...] | list[str] = (),
        **claims: Any,
    ) -> str:
        """Sign a token for `subject`.

        Extra keyword arguments become claims, so `issue("42", tenant="acme")`
        puts `tenant` in the payload and on `CurrentUser.claims`.
        """
        config = self.config
        now = datetime.now(UTC)
        if ttl is None:
            ttl = (
                config.access_token_ttl
                if token_type == ACCESS
                else config.refresh_token_ttl
            )

        payload: dict[str, Any] = {
            config.subject_claim: str(subject),
            config.token_type_claim: token_type,
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
            **claims,
        }
        if scopes:
            payload[config.scopes_claim] = list(scopes)
        if config.issuer:
            payload["iss"] = config.issuer
        if config.audience:
            payload["aud"] = config.audience

        return jwt.encode(payload, config.signing_key, algorithm=config.algorithm)

    def decode(
        self, token: str, *, expected_type: str | None = ACCESS
    ) -> dict[str, Any]:
        """Validate `token` and return its claims.

        `expected_type=None` skips the access/refresh check; pass `REFRESH` on
        the refresh endpoint so an access token cannot be traded for a new pair.
        """
        config = self.config
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                config.verification_key,
                algorithms=[config.algorithm],
                audience=config.audience,
                issuer=config.issuer,
                leeway=config.leeway,
                options={"require": ["exp", config.subject_claim]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized(
                "The access token has expired.",
                code="token_expired",
                headers=_challenge("The access token expired"),
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise Unauthorized(
                "The access token is not valid.",
                code="invalid_token",
                headers=_challenge("The access token is not valid"),
            ) from exc

        if expected_type is not None:
            actual = claims.get(config.token_type_claim, ACCESS)
            if actual != expected_type:
                raise Unauthorized(
                    f"Expected a {expected_type} token.",
                    code="wrong_token_type",
                    headers=_challenge(f"Expected a {expected_type} token"),
                )
        return claims


def _challenge(description: str) -> dict[str, str]:
    """The `WWW-Authenticate` header a 401 is supposed to carry."""
    return {
        "WWW-Authenticate": f'Bearer error="invalid_token", error_description="{description}"'
    }
