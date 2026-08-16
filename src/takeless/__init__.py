"""takeless — the mechanical half of a FastAPI service, as a dependency.

Two ways to use it, one implementation behind both.

The fast path, when you want the usual setup and want it in one place:

    from takeless import Takeless

    takeless = Takeless(
        settings=settings,
        logging={"level": "INFO"},
        cors={"allow_origins": ["https://myapp.com"]},
    )
    app = FastAPI()
    takeless.setup(app)

The modular path, when you want one piece and nothing else:

    from takeless.rate_limit import RateLimiter, RateLimitConfig

    RateLimiter(RateLimitConfig(default_limit="100/minute")).setup(app)

`Takeless` builds the very same objects the second form builds by hand, so
neither path has behaviour the other lacks. Import it and nothing optional is
imported with it: each module pulls its own backend the first time you use it,
and says which extra installs it if it is missing.
"""

from __future__ import annotations

from takeless.core.app import Takeless
from takeless.core.component import Check, Component
from takeless.core.deps import MissingDependencyError

__version__ = "0.1.0"

__all__ = [
    "Check",
    "Component",
    "MissingDependencyError",
    "Takeless",
    "__version__",
]
