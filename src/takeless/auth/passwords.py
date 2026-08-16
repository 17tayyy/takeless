"""Password hashing with argon2id."""

from __future__ import annotations

from typing import TYPE_CHECKING

from takeless.core.deps import require_dependency

if TYPE_CHECKING:
    from takeless.auth.config import AuthConfig

argon2 = require_dependency("argon2")
argon2_exceptions = require_dependency("argon2.exceptions")


class PasswordHasher:
    """Hash and verify passwords, with a rehash signal.

    `verify` never raises on a wrong password — it returns `False`, so a caller
    cannot accidentally turn a failed login into a 500.
    """

    def __init__(self, config: AuthConfig) -> None:
        self._hasher = argon2.PasswordHasher(
            time_cost=config.hash_time_cost,
            memory_cost=config.hash_memory_cost,
            parallelism=config.hash_parallelism,
        )

    def hash(self, password: str) -> str:
        """The argon2id encoded hash, parameters included."""
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        """Whether `password` matches `hashed`."""
        try:
            return self._hasher.verify(hashed, password)
        except (
            argon2_exceptions.VerifyMismatchError,
            argon2_exceptions.VerificationError,
            argon2_exceptions.InvalidHashError,
        ):
            return False

    def needs_rehash(self, hashed: str) -> bool:
        """True when `hashed` was made with weaker parameters than the current
        config. Check it after a successful login and re-store the hash — it is
        the only moment the plaintext is available to upgrade it."""
        try:
            return self._hasher.check_needs_rehash(hashed)
        except argon2_exceptions.InvalidHashError:
            return True
