"""Optional-dependency loading.

Every heavy module (`auth`, `jobs`, `db`, ...) lives behind a `pip install
takeless[extra]`. Importing it without the extra must fail with the install
command, not with a bare `ModuleNotFoundError` the caller has to decode.
"""

from __future__ import annotations

import importlib
from types import ModuleType

_EXTRA_BY_MODULE: dict[str, str] = {
    "jwt": "auth",
    "argon2": "auth",
    "ardiq": "jobs",
    "sqlalchemy": "db",
    "redis": "redis",
    "boto3": "aws",
    "botocore": "aws",
}


class MissingDependencyError(ImportError):
    """A takeless module was used without the extra that provides its backend."""


def require_dependency(module: str, *, extra: str | None = None) -> ModuleType:
    """Import `module`, or raise with the `pip install` line that provides it.

    `extra` overrides the built-in module→extra mapping; pass it for modules
    that are not in the table (a third-party backend, say).
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        root = module.partition(".")[0]
        extra = extra or _EXTRA_BY_MODULE.get(root)
        if extra is None:
            raise
        raise MissingDependencyError(
            f"{module!r} is not installed. It ships with the {extra!r} extra:\n"
            f"    pip install 'takeless[{extra}]'"
        ) from exc
