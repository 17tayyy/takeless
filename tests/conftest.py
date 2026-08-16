from __future__ import annotations

import pytest
import structlog

from takeless.settings.secrets import reset_secrets


@pytest.fixture(autouse=True)
def clean_process_state() -> None:
    """Both the secrets provider and the log context are process-wide, so a
    test that sets either must not leak into the next one."""
    reset_secrets()
    structlog.contextvars.clear_contextvars()
    yield
    reset_secrets()
    structlog.contextvars.clear_contextvars()
