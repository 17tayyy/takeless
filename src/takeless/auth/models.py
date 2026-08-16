from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CurrentUser(BaseModel):
    """The caller, as reconstructed from a validated access token.

    This is not a database row — nothing here was read from your storage. It is
    what the token asserts. Load the user record yourself when you need more
    than the token carries.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    email: str | None = None
    scopes: tuple[str, ...] = ()

    #: Every claim in the token, including the ones lifted into the fields
    #: above, for reading application-specific claims you added at issue time.
    claims: dict[str, Any] = Field(default_factory=dict, repr=False)

    #: The raw token, for forwarding to a downstream service.
    token: str = Field(default="", repr=False)

    def has_scope(self, *scopes: str) -> bool:
        """True when every named scope was granted."""
        return set(scopes).issubset(self.scopes)


class TokenPair(BaseModel):
    """What a login endpoint returns. Field names follow OAuth 2 so that
    off-the-shelf clients can consume it unchanged."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    #: Seconds until `access_token` expires.
    expires_in: int
