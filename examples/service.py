"""A whole service, runnable.

    uv run uvicorn examples.service:app --reload

Everything below the `Takeless(...)` call is application code. Everything the
setup buys you — `/health`, `/health/live`, the Scalar reference at `/docs`,
consistent error bodies, request-scoped structured logs, CORS, rate limiting —
is not written here because it does not need to be.

The `secret://` values resolve from the environment by default, so:

    export JWT_SIGNING_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from takeless import Takeless
from takeless.auth import AuthenticatedUser, require_scopes
from takeless.db import Session
from takeless.errors import NotFound, Unauthorized
from takeless.rate_limit import RateLimit
from takeless.settings import BaseAppSettings


class Settings(BaseAppSettings):
    app_name: str = "example-service"
    environment: str = "development"
    jwt_secret: str = "secret://jwt-signing-key"
    db_url: str = "sqlite+aiosqlite:///./example.db"


settings = Settings()

takeless = Takeless(
    settings=settings,
    logging={"level": "INFO"},
    auth={"secret": settings.jwt_secret},
    db={"url": settings.db_url},
    rate_limit={"default_limit": "100/minute"},
    cors={"allow_origins": ["https://myapp.com"]},
    docs={"provider": "scalar", "enabled_envs": ["development", "staging"]},
)

app = FastAPI(title=settings.app_name, version="1.0.0")
takeless.setup(app)


# A fake user store, so the example runs with nothing else installed.
USERS = {
    "ada@example.com": {"id": "1", "password": "lovelace", "scopes": ("reports:write",)}
}


@app.post("/login", dependencies=[Depends(RateLimit("5/minute"))])
async def login(email: str, password: str):
    """Tighter than the global limit, because this endpoint is guessable."""
    record = USERS.get(email)
    if record is None or record["password"] != password:
        # One message for both cases: which of the two failed is information
        # the caller has not earned.
        raise Unauthorized("Wrong email or password.")
    return takeless.auth.create_token_pair(
        record["id"], scopes=record["scopes"], email=email
    )


@app.get("/me")
async def get_me(user: AuthenticatedUser):
    # request_id and user_id are already on the log line; nothing to pass.
    takeless.logger.info("user_fetched_profile")
    return {"id": user.id, "email": user.email, "scopes": list(user.scopes)}


@app.get("/projects/{project_id}")
async def get_project(project_id: str, user: AuthenticatedUser, session: Session):
    del session  # a real handler would query here
    raise NotFound("No project with that id.", details={"project_id": project_id})


@app.post("/reports", dependencies=[Depends(require_scopes("reports:write"))])
async def generate_report(user: AuthenticatedUser):
    """With `takeless[jobs]` and a broker configured, this would be:

    job = await jobs.enqueue("generate_report", user_id=user.id)
    return {"job_id": job.task_id, "status": "queued"}
    """
    return {"status": "queued", "user_id": user.id}
