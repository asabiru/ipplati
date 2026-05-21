from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from app.schemas import BankTransactionBatchIn, ReceiptBatchIn, ReconciliationSummary, WalletDealBatchIn
from app.sync.config import sync_settings


@dataclass(slots=True)
class ApiCallResult:
    status_code: int
    response_body: str


class CoreApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: int = 30) -> None:
        self.base_url = (base_url or sync_settings.api_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _post(self, path: str, payload: dict | None = None) -> ApiCallResult:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
        response.raise_for_status()
        return ApiCallResult(status_code=response.status_code, response_body=response.text)

    def import_wallet(self, payload: WalletDealBatchIn) -> ApiCallResult:
        return self._post("/wallet/deals/import", payload.model_dump(mode="json"))

    def import_bank_transactions(self, payload: BankTransactionBatchIn) -> ApiCallResult:
        return self._post("/bank/transactions/import", payload.model_dump(mode="json"))

    def import_receipts(self, payload: ReceiptBatchIn) -> ApiCallResult:
        return self._post("/receipts/import", payload.model_dump(mode="json"))

    def pull_sber_statements(
        self,
        *,
        account_number: str | None,
        statement_date: date | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        all_pages: bool = True,
    ) -> ApiCallResult:
        payload: dict[str, object] = {
            "page": page,
            "all_pages": all_pages,
            "auto_import": True,
        }
        if account_number:
            payload["account_number"] = account_number
        if statement_date is not None:
            payload["statement_date"] = statement_date.isoformat()
        if date_from is not None:
            payload["date_from"] = date_from.isoformat()
        if date_to is not None:
            payload["date_to"] = date_to.isoformat()
        return self._post("/sber/statements/pull", payload)

    def run_matching(self) -> ApiCallResult:
        return self._post("/matching/run")

    def run_receipt_reconciliation(self) -> ApiCallResult:
        return self._post("/receipts/reconcile")

    def run_classification(self) -> ApiCallResult:
        return self._post("/classification/run")

    def get_report(self) -> ReconciliationSummary:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/reports/reconciliation")
        response.raise_for_status()
        return ReconciliationSummary.model_validate(response.json())
