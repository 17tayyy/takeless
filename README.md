<h1 align="center">takeless</h1>

<p align="center">
  <i>The mechanical half of a FastAPI service — installed, not copy-pasted.</i>
</p>

<p align="center">
  <a href="https://pypi.org/project/takeless/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/takeless.svg"></a>
  <a href="https://pypi.org/project/takeless/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/takeless.svg"></a>
  <a href="https://github.com/17tayyy/takeless/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/17tayyy/takeless/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/17tayyy/takeless/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
</p>

<p align="center">
  <a href="#install">Install</a> &nbsp;•&nbsp;
  <a href="#the-two-paths">The two paths</a> &nbsp;•&nbsp;
  <a href="#modules">Modules</a> &nbsp;•&nbsp;
  <a href="examples/service.py">Example service</a> &nbsp;•&nbsp;
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

Every FastAPI service ends up with the same 150 lines before it does anything
of its own: JWT plumbing, a structlog setup, a session dependency, exception
handlers, a health endpoint, CORS, rate limiting, a secrets lookup, and docs
you have to remember to switch off in production.

Boilerplate repos hand you those lines to clone and then own forever. **takeless
is a dependency**: you `pip install` it, you upgrade it, and the 150 lines stay
someone else's problem.

It is not a framework. It does not wrap FastAPI, replace its router, or ask you
to write handlers differently — every module here is ordinary FastAPI
middleware, dependencies and exception handlers, assembled for you.

```python
from fastapi import FastAPI
from takeless import Takeless
from takeless.auth import AuthenticatedUser
from takeless.jobs import JobsClient
from takeless.settings import BaseAppSettings


class Settings(BaseAppSettings):
    app_name: str = "my-service"
    jwt_secret: str = "secret://jwt-signing-key"  # resolved via the secrets provider
    db_url: str = "secret://prod-db#url"


settings = Settings()

takeless = Takeless(
    settings=settings,
    logging={"level": "INFO"},
    auth={"secret": settings.jwt_secret},
    db={"url": settings.db_url},
    jobs={"broker_url": "redis://localhost:6379", "queue": "default"},
    rate_limit={
        "backend": "redis",
        "url": "redis://localhost:6379",
        "default_limit": "100/minute",
    },
    cors={"allow_origins": ["https://myapp.com"]},
    secrets={"provider": "aws", "region": "eu-west-1"},
    docs={"provider": "scalar", "enabled_envs": ["development", "staging"]},
)

app = FastAPI(title=settings.app_name)
takeless.setup(app)


@app.get("/me")
async def get_me(user: AuthenticatedUser):
    takeless.logger.info("user_fetched_profile")  # request_id and user_id already bound
    return {"id": user.id, "email": user.email}


@app.post("/reports")
async def generate_report(user: AuthenticatedUser, jobs: JobsClient):
    job = await jobs.enqueue("generate_report", user_id=user.id)
    return {"job_id": job.task_id, "status": "queued"}
```

That gives you, with no further code: `GET /health` aggregating every
configured service, `GET /health/live` for liveness, a consistent JSON error
body on every failure, structured logs carrying the request id and the
authenticated user, CORS, global rate limiting, and a Scalar API reference that
disappears in production.

## Install

```bash
pip install takeless                    # core: FastAPI, settings, logging, errors, health, docs, cors
pip install 'takeless[auth]'            # + PyJWT, argon2
pip install 'takeless[jobs]'            # + ArdiQ
pip install 'takeless[db]'              # + SQLAlchemy async
pip install 'takeless[redis]'           # + Redis-backed rate limiting
pip install 'takeless[aws]'             # + AWS Secrets Manager
pip install 'takeless[all]'
```

One package, extras per backend. `import takeless` pulls in none of them —
each module imports its backend the first time you use it, and tells you which
extra installs it if it is missing.

## The two paths

`Takeless` is sugar. It coerces each keyword into that module's config,
constructs the module, and calls `setup(app)` on it. Nothing else.

So every module also works alone, with no `Takeless` object anywhere:

```python
from takeless.rate_limit import RateLimiter, RateLimitConfig

RateLimiter(RateLimitConfig(default_limit="100/minute")).setup(app)
```

Adopt one module in an existing service, or all of them in a new one; there is
no all-or-nothing switch, and no second implementation to drift.

## Modules

| Module | What it gives you | Extra |
|---|---|---|
| `takeless.settings` | pydantic-settings plus `secret://` references resolved against a secrets manager | — (`aws` for AWS) |
| `takeless.observability` | structlog, JSON in production, request id and user id bound automatically | — |
| `takeless.errors` | An exception hierarchy and handlers producing one JSON shape for every failure | — |
| `takeless.health` | `/health` aggregating whatever you configured, `/health/live` for liveness | — |
| `takeless.docs` | Scalar instead of Swagger UI, removed entirely outside allowed environments | — |
| `takeless.cors` | CORS with defaults that refuse rather than permit | — |
| `takeless.auth` | JWT issue/verify, argon2 passwords, `require_auth` / `require_scopes` | `auth` |
| `takeless.db` | Async engine, session factory, request-scoped session dependency | `db` |
| `takeless.jobs` | A connected [ArdiQ](https://github.com/17tayyy/ardiq) producer, enqueue by name | `jobs` |
| `takeless.rate_limit` | Global and per-route limits, memory or Redis | `redis` for Redis |

### settings

A field whose value starts with `secret://` is fetched from the configured
provider instead of being taken literally, whether that value came from a
default or from an environment variable.

```python
class Settings(BaseAppSettings):
    jwt_secret: str = "secret://jwt-signing-key"
    db_url: str = "secret://prod-db#url"  # -> the "url" key of a JSON secret
```

The provider is process-wide and chosen by environment, not by code — the same
image reads a `.env` locally and Secrets Manager in production:

```bash
TAKELESS_SECRETS_PROVIDER=aws
TAKELESS_SECRETS_REGION=eu-west-1
```

The default provider is `env`: `secret://jwt-signing-key` reads
`$JWT_SIGNING_KEY`, so a service written against a secrets manager still runs
locally with no cloud credentials. `configure_secrets(provider="aws",
region="eu-west-1")` is the programmatic form — **call it before the first
`Settings()`**, since resolution happens at instantiation. `Takeless(secrets=...)`
calls it too, but only affects settings built afterwards.

The interface is `SecretsProvider`; AWS is the only implementation shipped, and
adding another is one class.

### auth

```python
from takeless.auth import Auth, AuthConfig, AuthenticatedUser, require_scopes

auth = Auth(AuthConfig(secret="...", access_token_ttl=timedelta(minutes=15)))
auth.setup(app)


@app.post("/login")
async def login(email: str, password: str):
    user = await find_user(email)
    if not user or not auth.verify_password(password, user.password_hash):
        raise Unauthorized("Wrong email or password.")
    return auth.create_token_pair(user.id, scopes=("reports:read",), email=user.email)


@app.get("/me")
async def me(user: AuthenticatedUser):
    return {"id": user.id}


@app.delete("/users/{id}", dependencies=[Depends(require_scopes("users:write"))])
async def delete_user(id: str): ...
```

`CurrentUser` is what the token asserts, not a database row — load your own
record when you need more than the token carries. Authenticating binds
`user_id` onto the log context, which is why endpoint log lines are
attributable without passing anything.

Asymmetric algorithms are supported through `private_key` / `public_key`, so a
service that only validates tokens ships without the signing key.

### errors

```python
from takeless.errors import NotFound

raise NotFound("No project with that id.", details={"id": project_id})
```

```json
{"error": {"code": "not_found", "message": "No project with that id.",
           "details": {"id": "..."}, "request_id": "9f2c..."}}
```

The same shape covers `HTTPException` raised by third-party dependencies,
request validation failures, and unhandled exceptions — for which the exception
text is withheld unless you turn `expose_internal_errors` on.

### health

You never list what to probe. Every component that can be probed implements
`check()`, so configuring a database is what puts the database in `/health`.

```json
{"status": "ok",
 "checks": {"db": {"status": "ok", "latency_ms": 1.2, "dialect": "postgresql"},
            "jobs": {"status": "ok", "queue": "default", "queued": 3}}}
```

`/health` returns 503 when anything is unhealthy. `/health/live` never probes a
dependency, so a flapping database cannot get your pods restarted.

### rate_limit

```python
@app.post("/login", dependencies=[Depends(RateLimit("5/minute"))])
async def login(): ...
```

Fixed-window counters, `X-RateLimit-*` headers on every response, `Retry-After`
on refusals. The memory backend counts per process — with more than one replica
point it at Redis and the limit becomes the number you actually wrote.

### docs

Scalar replaces Swagger UI. Outside `enabled_envs` the documentation *and* the
OpenAPI schema are removed from the app — not hidden behind a 403, removed, so
there is no schema left to leak. Production is not on the default list.

## Requirements

Python 3.12+, FastAPI 0.115+.

## License

MIT
