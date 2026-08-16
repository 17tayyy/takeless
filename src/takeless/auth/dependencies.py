"""Request-scoped auth dependencies.

These are plain importable functions rather than something you build per app.
They find the `Auth` component through `request.app.state`, which keeps them
usable straight out of the import while still letting two apps in one process
sign with different keys.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from takeless.auth.component import Auth
from takeless.auth.models import CurrentUser
from takeless.core.component import get_component
from takeless.errors.exceptions import Forbidden, Unauthorized
from takeless.observability.context import bind_context

#: `auto_error=False` so a missing header reaches our handler and comes back in
#: the takeless error envelope. Declaring it at all is what puts the bearer
#: scheme — and the padlock — into the OpenAPI document.
_bearer = HTTPBearer(
    auto_error=False, scheme_name="bearerAuth", description="JWT access token"
)

_Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def optional_auth(
    request: Request, credentials: _Credentials
) -> CurrentUser | None:
    """The caller if they presented a valid token, `None` if they presented
    none. An invalid token is still a 401 — silently treating a broken token as
    anonymous hides bugs and makes expiry look like a permissions problem."""
    if credentials is None:
        return None
    return _authenticate(request, credentials.credentials)


async def require_auth(request: Request, credentials: _Credentials) -> CurrentUser:
    """The caller, or 401."""
    if credentials is None:
        raise Unauthorized(
            "This endpoint requires a bearer token.",
            code="missing_token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _authenticate(request, credentials.credentials)


def require_scopes(
    *scopes: str,
) -> Callable[..., Coroutine[Any, Any, CurrentUser]]:
    """A dependency demanding every named scope.

    @app.delete("/users/{id}", dependencies=[Depends(require_scopes("users:write"))])
    """

    async def dependency(
        user: Annotated[CurrentUser, Depends(require_auth)],
    ) -> CurrentUser:
        if not user.has_scope(*scopes):
            raise Forbidden(
                "This endpoint requires additional permissions.",
                code="insufficient_scope",
                details={"required": list(scopes), "granted": list(user.scopes)},
            )
        return user

    return dependency


def _authenticate(request: Request, token: str) -> CurrentUser:
    user = get_component(request.app, Auth).user_from_token(token)
    # From here on every log line in this request carries the user, which is
    # what makes `logger.info("...")` in an endpoint attributable for free.
    bind_context(user_id=user.id)
    return user


AuthenticatedUser = Annotated[CurrentUser, Depends(require_auth)]
OptionalUser = Annotated[CurrentUser | None, Depends(optional_auth)]
