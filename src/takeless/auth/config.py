from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MINIMUM_SECRET_BYTES = {"HS256": 32, "HS384": 48, "HS512": 64}


class AuthConfig(BaseModel):
    """Token signing and password hashing parameters."""

    model_config = ConfigDict(extra="forbid")

    #: Signing key for the HMAC algorithms (HS256 and friends). Ignored for
    #: asymmetric algorithms, which use the key pair below.
    secret: str | None = None

    #: PEM private key used to sign, and public key used to verify, when
    #: `algorithm` is asymmetric (RS*, ES*, EdDSA). Splitting them lets a
    #: service that only validates tokens ship without the signing key.
    private_key: str | None = None
    public_key: str | None = None

    algorithm: str = "HS256"

    access_token_ttl: timedelta = timedelta(minutes=15)
    refresh_token_ttl: timedelta = timedelta(days=30)

    issuer: str | None = None
    audience: str | None = None

    #: Clock skew tolerated when checking `exp` / `nbf`.
    leeway: timedelta = timedelta(seconds=0)

    #: Claim carrying the user id, and the one carrying granted scopes.
    subject_claim: str = "sub"
    scopes_claim: str = "scopes"
    #: Claim distinguishing an access token from a refresh token, so a refresh
    #: token cannot be replayed as a bearer credential.
    token_type_claim: str = "token_type"

    #: Claims lifted onto `CurrentUser` when present.
    email_claim: str = "email"

    # argon2id parameters. The defaults are argon2-cffi's, which track the
    # OWASP recommendation; raise `memory_cost` before `time_cost` if you want
    # a slower hash.
    hash_time_cost: int = Field(default=3, ge=1)
    hash_memory_cost: int = Field(default=65536, ge=8)
    hash_parallelism: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def _check_keys(self) -> AuthConfig:
        algorithm = self.algorithm.upper()
        symmetric = algorithm.startswith("HS")
        if symmetric and not self.secret:
            raise ValueError(
                f"auth: algorithm {self.algorithm!r} signs with a shared secret, "
                f"so secret=... is required"
            )
        if symmetric:
            minimum = _MINIMUM_SECRET_BYTES.get(algorithm, 32)
            length = len(self.secret.encode()) if self.secret else 0
            if length < minimum:
                raise ValueError(
                    f"auth: the {self.algorithm} secret is {length} bytes; "
                    f"RFC 7518 requires at least {minimum}. Generate one with "
                    f'`python -c "import secrets; print(secrets.token_urlsafe({minimum}))"`.'
                )
        if not symmetric and not (self.private_key or self.public_key):
            raise ValueError(
                f"auth: algorithm {self.algorithm!r} needs private_key= to issue "
                f"tokens, public_key= to verify them, or both"
            )
        if self.hash_memory_cost < 8 * self.hash_parallelism:
            raise ValueError(
                f"auth: argon2 needs hash_memory_cost >= 8 * hash_parallelism "
                f"({8 * self.hash_parallelism}), got {self.hash_memory_cost}"
            )
        return self

    @property
    def signing_key(self) -> str:
        key = (
            self.secret if self.algorithm.upper().startswith("HS") else self.private_key
        )
        if not key:
            raise ValueError(
                "auth: this service has no signing key, so it can only verify "
                "tokens. Pass private_key= to issue them."
            )
        return key

    @property
    def verification_key(self) -> str:
        key = (
            self.secret if self.algorithm.upper().startswith("HS") else self.public_key
        )
        if not key:
            raise ValueError("auth: no verification key configured")
        return key
