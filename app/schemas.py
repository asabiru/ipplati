from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import DealStatus, ReceiptStatus, ReceiptType, TransactionDirection


class WalletDealIn(BaseModel):
    external_id: str
    expected_fiat_amount: Decimal = Field(..., gt=0)
    fiat_currency: str = "RUB"
    counterparty_name: str | None = None
    counterparty_wallet_id: str | None = None
    payment_method: str | None = None
    opened_at: datetime | None = None
    completed_at: datetime | None = None
    status: DealStatus = DealStatus.detected
    notes: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class WalletDealBatchIn(BaseModel):
    deals: list[WalletDealIn]


class BankTransactionIn(BaseModel):
    external_id: str
    amount: Decimal = Field(..., gt=0)
    currency: str = "RUB"
    direction: TransactionDirection = TransactionDirection.incoming
    payer_name: str | None = None
    payer_account_masked: str | None = None
    reference: str | None = None
    booked_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class BankTransactionBatchIn(BaseModel):
    transactions: list[BankTransactionIn]
    provider: str = "sber"


class ReceiptIn(BaseModel):
    external_id: str
    deal_external_id: str | None = None
    receipt_type: ReceiptType
    status: ReceiptStatus = ReceiptStatus.issued
    amount: Decimal = Field(..., gt=0)
    issued_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ReceiptBatchIn(BaseModel):
    receipts: list[ReceiptIn]


class MatchRunResult(BaseModel):
    deal_external_id: str
    bank_transaction_external_id: str | None = None
    score: int
    outcome: str
    rule_hits: dict[str, Any]


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    source: str
    status: DealStatus
    fiat_currency: str
    expected_fiat_amount: Decimal
    counterparty_name: str | None
    counterparty_wallet_id: str | None
    payment_method: str | None
    opened_at: datetime | None
    completed_at: datetime | None
    return_reason: str | None
    return_detected_at: datetime | None
    notes: str | None
    raw_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BankTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    provider: str
    amount: Decimal
    currency: str
    direction: TransactionDirection
    payer_name: str | None
    payer_account_masked: str | None
    reference: str | None
    booked_at: datetime
    deal_id: str | None
    raw_payload: dict[str, Any]
    created_at: datetime


class ManualDealCreateIn(BaseModel):
    bank_transaction_external_id: str
    side: str = Field(..., pattern="^(buy|sell)$")
    rate: Decimal = Field(..., gt=0)
    crypto_currency: str = "USDT"
    comment: str | None = None
    counterparty_name: str | None = None
    payment_method: str | None = "manual"


class ManualDealCreateOut(BaseModel):
    deal: DealOut
    bank_transaction: BankTransactionOut
    created: bool


class ReconciliationSummary(BaseModel):
    total_deals: int
    matched_deals: int
    receipted_deals: int
    return_detected_deals: int
    third_party_review_deals: int
    unmatched_bank_transactions: int
    total_expected_rub: Decimal
    total_received_rub: Decimal
    total_returned_rub: Decimal


class WalletRateStats(BaseModel):
    completed_deals_with_rates: int
    crypto_currency: str | None = None
    total_fiat_amount: Decimal
    gross_crypto_amount: Decimal
    total_net_crypto_amount: Decimal
    total_fee_crypto_amount: Decimal
    weighted_listed_rate: Decimal | None = None
    weighted_net_rate: Decimal | None = None


class WalletProfitStats(BaseModel):
    crypto_currency: str | None = None
    buy_fiat_amount: Decimal
    sell_fiat_amount: Decimal
    buy_net_crypto_amount: Decimal
    sell_net_crypto_amount: Decimal
    matched_crypto_amount: Decimal
    inventory_delta_crypto_amount: Decimal
    avg_buy_net_rate: Decimal | None = None
    avg_sell_net_rate: Decimal | None = None
    spread_per_crypto: Decimal | None = None
    estimated_profit_rub: Decimal
    cash_flow_delta_rub: Decimal


class WalletTradingStats(BaseModel):
    crypto_currency: str | None = None
    matched_buy_deals: int
    matched_sell_deals: int
    buy_turnover_rub: Decimal
    sell_turnover_rub: Decimal
    total_turnover_rub: Decimal
    avg_buy_net_rate: Decimal | None = None
    avg_sell_net_rate: Decimal | None = None
    realized_profit_rub: Decimal
    realized_sell_crypto_amount: Decimal
    balance_crypto_amount: Decimal


class TelegramDashboardSnapshot(BaseModel):
    summary: ReconciliationSummary
    wallet_rate_stats: WalletRateStats
    wallet_buy_rate_stats: WalletRateStats
    wallet_sell_rate_stats: WalletRateStats
    wallet_profit_stats: WalletProfitStats
    wallet_trading_stats: WalletTradingStats
    status_counts: dict[str, int]
    total_bank_transactions: int
    total_receipts: int
    sale_receipts: int
    refund_receipts: int
    date_from: date | None = None
    date_to: date | None = None
    wallet_csv_has_coverage: bool
    wallet_csv_message: str
    wallet_csv_files: list[str]
    wallet_csv_missing_dates: list[date]
    unmatched_wallet_deals_count: int
    unmatched_wallet_deal_ids: list[str]
    recent_deals: list[DealOut]
