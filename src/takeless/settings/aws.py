"""AWS Secrets Manager provider.

The first concrete provider. It exists behind the `SecretsProvider` protocol so
that adding GCP or Vault later is a new file, not a redesign — nothing outside
this module knows which cloud is answering.
"""

from __future__ import annotations

from typing import Any

from takeless.core.deps import require_dependency
from takeless.settings.secrets import SecretResolutionError


class AwsSecretsProvider:
    """Fetches secrets from AWS Secrets Manager.

    Credentials come from the standard boto3 chain (instance role, env vars,
    profile) — this never takes keys as arguments, so nothing here can end up
    hardcoded in a settings file.
    """

    def __init__(
        self,
        region: str | None = None,
        *,
        endpoint_url: str | None = None,
        profile: str | None = None,
        version_stage: str = "AWSCURRENT",
        **client_options: Any,
    ) -> None:
        self.region = region
        self.endpoint_url = endpoint_url
        self.profile = profile
        self.version_stage = version_stage
        self._client_options = client_options
        self._client: Any = None

    @property
    def client(self) -> Any:
        """The boto3 client, built on first use so importing this module costs
        nothing and a misconfigured region fails where it is used."""
        if self._client is None:
            boto3 = require_dependency("boto3")
            session = (
                boto3.Session(profile_name=self.profile)
                if self.profile
                else boto3.Session()
            )
            self._client = session.client(
                "secretsmanager",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                **self._client_options,
            )
        return self._client

    def get(self, name: str) -> str:
        botocore_exceptions = require_dependency("botocore.exceptions")
        try:
            response = self.client.get_secret_value(
                SecretId=name, VersionStage=self.version_stage
            )
        except botocore_exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise SecretResolutionError(
                f"AWS Secrets Manager refused secret {name!r} ({code}). "
                f"Check the name, the region, and that the caller's role has "
                f"secretsmanager:GetSecretValue on it."
            ) from exc

        if "SecretString" in response:
            return str(response["SecretString"])

        raise SecretResolutionError(
            f"secret {name!r} is stored as binary; takeless resolves string secrets"
        )

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()
