"""Business documents (invoices, reports) stored in the company bucket."""

import json
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .errors import DependencyFailure


class ArtifactStore:
    def __init__(self, s3_client: Any, bucket: str | None) -> None:
        self._s3 = s3_client
        self._bucket = bucket

    def put_json(self, key: str, document: dict[str, Any]) -> str | None:
        """Returns the key written, or None when no bucket is configured (local development)."""
        if not self._bucket:
            return None
        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=json.dumps(document, indent=2, default=str).encode(),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as exc:
            raise DependencyFailure(f"S3 unavailable: {exc}") from exc
        return key
