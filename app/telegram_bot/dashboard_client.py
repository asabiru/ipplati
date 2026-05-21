from __future__ import annotations

from datetime import date

import httpx

from app.schemas import BankTransactionOut, DealOut, ManualDealCreateOut, TelegramDashboardSnapshot
from app.telegram_bot.config import telegram_bot_settings


class DashboardApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: int = 30) -> None:
        self.base_url = (base_url or telegram_bot_settings.api_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_dashboard(
        self,
        recent_deals_limit: int | None = None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> TelegramDashboardSnapshot:
        params = {"recent_deals_limit": recent_deals_limit or telegram_bot_settings.recent_deals_limit}
        if date_from is not None:
            params["date_from"] = date_from.isoformat()
        if date_to is not None:
            params["date_to"] = date_to.isoformat()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/reports/dashboard", params=params)
        response.raise_for_status()
        return TelegramDashboardSnapshot.model_validate(response.json())

    def get_recent_deals(
        self,
        limit: int | None = None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DealOut]:
        dashboard = self.get_dashboard(
            recent_deals_limit=limit or telegram_bot_settings.recent_deals_limit,
            date_from=date_from,
            date_to=date_to,
        )
        return dashboard.recent_deals

    def get_unmatched_bank_transactions(self, limit: int = 10) -> list[BankTransactionOut]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/bank/transactions/unmatched", params={"limit": limit})
        response.raise_for_status()
        return [BankTransactionOut.model_validate(item) for item in response.json()]

    def create_manual_deal(
        self,
        *,
        bank_transaction_external_id: str,
        side: str,
        rate: str,
        comment: str | None = None,
        crypto_currency: str = "USDT",
    ) -> ManualDealCreateOut:
        payload = {
            "bank_transaction_external_id": bank_transaction_external_id,
            "side": side,
            "rate": rate,
            "comment": comment,
            "crypto_currency": crypto_currency,
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/manual/deals", json=payload)
        response.raise_for_status()
        return ManualDealCreateOut.model_validate(response.json())

    def get_kudir_xlsx(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[bytes, str]:
        params: dict[str, str] = {}
        if date_from is not None:
            params["date_from"] = date_from.isoformat()
        if date_to is not None:
            params["date_to"] = date_to.isoformat()
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/reports/kudir/xlsx", params=params)
        response.raise_for_status()
        content_disposition = response.headers.get("content-disposition", "")
        filename = "kudir.xlsx"
        if 'filename="' in content_disposition:
            filename = content_disposition.split('filename="')[1].rstrip('"')
        return response.content, filename
