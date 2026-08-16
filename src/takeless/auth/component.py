from __future__ import annotations

from datetime import timedelta
from typing import Any

from takeless.auth.config import AuthConfig
from takeless.auth.models import CurrentUser, TokenPair
from takeless.auth.passwords import PasswordHasher
from takeless.auth.tokens import ACCESS, REFRESH, TokenIssuer
from takeless.core.component import Component


class Auth(Component):
    """Token issuing, token validation and password hashing.

    Usable with no FastAPI app at all — a CLI that mints service tokens can
    construct this and call `create_access_token`. `setup(app)` only makes it
    reachable from the request-scoped dependencies.
    """

    name = "auth"

    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self.tokens = TokenIssuer(config)
        self.passwords = PasswordHasher(config)

    def create_access_token(
        self,
        subject: str,
        *,
        scopes: tuple[str, ...] | list[str] = (),
        ttl: timedelta | None = None,
        **claims: Any,
    ) -> str:
        return self.tokens.issue(
            subject, token_type=ACCESS, ttl=ttl, scopes=scopes, **claims
        )

    def create_refresh_token(
        self, subject: str, *, ttl: timedelta | None = None, **claims: Any
    ) -> str:
        return self.tokens.issue(subject, token_type=REFRESH, ttl=ttl, **claims)

    def create_token_pair(
        self,
        subject: str,
        *,
        scopes: tuple[str, ...] | list[str] = (),
        **claims: Any,
    ) -> TokenPair:
        """Both tokens plus the access token's lifetime, ready to return from a
        login endpoint."""
        return TokenPair(
            access_token=self.create_access_token(subject, scopes=scopes, **claims),
            refresh_token=self.create_refresh_token(subject),
            expires_in=int(self.config.access_token_ttl.total_seconds()),
        )

    def decode(
        self, token: str, *, expected_type: str | None = ACCESS
    ) -> dict[str, Any]:
        """Validated claims, or `Unauthorized`."""
        return self.tokens.decode(token, expected_type=expected_type)

    def user_from_token(
        self, token: str, *, expected_type: str | None = ACCESS
    ) -> CurrentUser:
        """Validate `token` and shape its claims into a `CurrentUser`."""
        claims = self.decode(token, expected_type=expected_type)
        config = self.config
        raw_scopes = claims.get(config.scopes_claim) or ()
        if isinstance(raw_scopes, str):
            raw_scopes = raw_scopes.split()
        return CurrentUser(
            id=str(claims[config.subject_claim]),
            email=claims.get(config.email_claim),
            scopes=tuple(raw_scopes),
            claims=claims,
            token=token,
        )

    def hash_password(self, password: str) -> str:
        return self.passwords.hash(password)

    def verify_password(self, password: str, hashed: str) -> bool:
        return self.passwords.verify(password, hashed)

    def password_needs_rehash(self, hashed: str) -> bool:
        return self.passwords.needs_rehash(hashed)
