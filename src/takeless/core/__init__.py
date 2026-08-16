"""The wiring layer: the `Component` contract every module implements, and the
`Takeless` object that instantiates and connects them.

Nothing here imports an optional dependency at module level — `import takeless`
must work on a bare `pip install takeless`.
"""

from __future__ import annotations

from takeless.core.app import Takeless
from takeless.core.component import Check, Component, get_component, register
from takeless.core.deps import MissingDependencyError, require_dependency

__all__ = [
    "Check",
    "Component",
    "MissingDependencyError",
    "Takeless",
    "get_component",
    "register",
    "require_dependency",
]
