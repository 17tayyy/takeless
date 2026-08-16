# Contributing to takeless

Thanks for taking the time. Bug reports, docs fixes and features are all welcome.

## Getting set up

You need [uv](https://docs.astral.sh/uv/). Nothing else — the test suite runs
against SQLite and in-process fakes, so there is no broker or database to start.

```bash
git clone https://github.com/17tayyy/takeless
cd takeless
uv sync --all-extras
uv run pytest
```

To see it actually serve traffic:

```bash
export JWT_SIGNING_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uv run uvicorn examples.service:app --reload
```

## Before you open a pull request

```bash
uv run ruff check .
uv run ruff format .
uv run ty check src/
uv run pytest
```

CI runs those on 3.12 and 3.13, plus one extra job described below.

## The two rules that shape this codebase

**1. `import takeless` must not import a backend.**

The headline claim is that `pip install takeless` pulls only FastAPI,
pydantic-settings and structlog. A stray top-level `import jwt` in a shared
module quietly breaks that for everyone, so CI installs the package with no
extras and asserts that none of `jwt`, `argon2`, `ardiq`, `sqlalchemy`, `redis`
or `boto3` is importable.

A module that needs an extra calls `require_dependency()` at *its own* module
level, so importing it without the extra fails with the `pip install` line
rather than succeeding and failing later at construction time.

**2. `Takeless` holds no logic.**

Every module implements the `Component` contract in `src/takeless/core/component.py`:
`setup(app)`, `startup()`, `shutdown()`, `check()`. The `Takeless` object only
coerces each keyword into that module's config, builds it, and calls those four
methods — which is what keeps the modular path (`Auth(...).setup(app)`) and the
central path from drifting apart.

If you add a module:

- put it behind a `*Config` pydantic model with `extra="forbid"`, so a typo in
  a config dict is an error rather than a silently ignored key;
- have it register itself via `super().setup(app)` so request-scoped
  dependencies can find it on `app.state`;
- implement `check()` if it talks to anything over a network — that is the only
  thing needed to make it appear in `/health`;
- add a `_build_*` method to `Takeless` and a name to `_SETUP_ORDER`.

## Conventions

- Comments explain *why*, not *what*. If a line's reasoning is obvious from
  reading it, it does not need a comment above it.
- Config fields carry a `#:` comment when the default embodies a decision
  someone might otherwise reverse by accident.
- Error messages say what to do next, not just what went wrong.
- Tests are named after the behaviour they pin, in a sentence.

## Releasing

Maintainers only. Bump `version` in `pyproject.toml`, commit, then:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The tag triggers `.github/workflows/release.yml`, which builds the sdist and
wheel, publishes to PyPI through Trusted Publishing (no token in the repo) and
opens a GitHub Release with generated notes.
