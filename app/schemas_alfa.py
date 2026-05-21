from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class AlfaCallbackPayload(BaseModel):
    code: str | None = None
    state: str | None = None
    error: str | None = None
    error_description: str | None = None


class AlfaTokenOut(BaseModel):
    subject: str | None = None
    token_type: str | None = None
    scope: str | None = None
    expires_at: str | None = None
    obtained_at: str | None = None


class AlfaStatusOut(BaseModel):
    configured: bool
    redirect_uri: str
    authorize_url: str
    token_url: str
    scope: str
    has_client_secret: bool
    has_api_key: bool
    has_client_certificate: bool
    token: AlfaTokenOut | None = None


class AlfaStatementPullIn(BaseModel):
    account_number: str | None = None
    statement_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    all_pages: bool = True
    auto_import: bool = True


class AlfaStatementPullOut(BaseModel):
    account_number: str
    statement_date: date | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int
    pages_processed: int
    days_processed: int
    imported: int
    total_transactions: int
    raw: dict[str, Any]


class AlfaStatementTransactionOut(BaseModel):
    external_id: str
    amount: Decimal
    currency: str
    direction: str
    payer_name: str | None = None
    payer_account_masked: str | None = None
    reference: str | None = None
