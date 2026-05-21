from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.reader.config import reader_settings
from app.schemas import WalletDealBatchIn


@dataclass(slots=True)
class ReaderImportResult:
    imported_count: int
    status_code: int
    response_body: str


class WalletApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: int | None = None) -> None:
        self.base_url = (base_url or reader_settings.api_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or reader_settings.request_timeout_seconds

    def import_deals(self, payload: WalletDealBatchIn) -> ReaderImportResult:
        endpoint = f"{self.base_url}/wallet/deals/import"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(endpoint, json=payload.model_dump(mode="json"))

        response.raise_for_status()
        response_data = response.json()
        return ReaderImportResult(
            imported_count=len(response_data),
            status_code=response.status_code,
            response_body=response.text,
        )
