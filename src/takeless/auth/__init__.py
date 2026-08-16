"""JWT authentication and password hashing.

Requires `pip install 'takeless[auth]'`.

Standalone:

    from takeless.auth import Auth, AuthConfig, AuthenticatedUser

    auth = Auth(AuthConfig(secret="..."))
    auth.setup(app)

    @app.post("/login")
    async def login(email: str, password: str):
        user = await find_user(email)
        if not user or not auth.verify_password(password, user.password_hash):
            raise Unauthorized("Wrong email or password.")
        return auth.create_token_pair(user.id, scopes=("read",), email=user.email)

    @app.get("/me")
    async def me(user: AuthenticatedUser):
        return {"id": user.id}
"""

from __future__ import annotations

from takeless.auth.component import Auth
from takeless.auth.config import AuthConfig
from takeless.auth.dependencies import (
    AuthenticatedUser,
    OptionalUser,
    optional_auth,
    require_auth,
    require_scopes,
)
from takeless.auth.models import CurrentUser, TokenPair
from takeless.auth.passwords import PasswordHasher
from takeless.auth.tokens import ACCESS, REFRESH, TokenIssuer

__all__ = [
    "ACCESS",
    "REFRESH",
    "Auth",
    "AuthConfig",
    "AuthenticatedUser",
    "CurrentUser",
    "OptionalUser",
    "PasswordHasher",
    "TokenIssuer",
    "TokenPair",
    "optional_auth",
    "require_auth",
    "require_scopes",
]
