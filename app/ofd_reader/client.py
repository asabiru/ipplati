from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.schemas import ReceiptBatchIn
from app.ofd_reader.config import ofd_reader_settings


@dataclass(slots=True)
class OfdImportResult:
    imported_count: int
    status_code: int
    response_body: str


class OfdApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: int | None = None) -> None:
        self.base_url = (base_url or ofd_reader_settings.api_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or ofd_reader_settings.request_timeout_seconds

    def import_receipts(self, payload: ReceiptBatchIn) -> OfdImportResult:
        endpoint = f"{self.base_url}/receipts/import"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(endpoint, json=payload.model_dump(mode="json"))

        response.raise_for_status()
        response_data = response.json()
        return OfdImportResult(
            imported_count=int(response_data.get("imported", 0)),
            status_code=response.status_code,
            response_body=response.text,
        )
