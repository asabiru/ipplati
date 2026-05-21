from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.models import DealStatus
from app.schemas import WalletDealBatchIn, WalletDealIn


STATUS_MAP = {
    "new": DealStatus.awaiting_payment,
    "active": DealStatus.awaiting_payment,
    "paid": DealStatus.awaiting_payment,
    "awaiting_payment": DealStatus.awaiting_payment,
    "completed": DealStatus.matched,
    "matched": DealStatus.matched,
    "cancelled": DealStatus.canceled,
    "canceled": DealStatus.canceled,
    "disputed": DealStatus.third_party_review,
    "third_party_review": DealStatus.third_party_review,
    "refund": DealStatus.return_detected,
    "refunded": DealStatus.return_detected,
    "returned": DealStatus.return_detected,
    "reversed": DealStatus.return_detected,
    "chargeback": DealStatus.return_detected,
}


def _to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        return Decimal(str(value))
    raise ValueError(f"Unsupported amount value: {value!r}")


def map_status(raw_status: Any) -> DealStatus:
    if raw_status is None:
        return DealStatus.detected
    value = str(raw_status).strip().lower()
    return STATUS_MAP.get(value, DealStatus.detected)


def normalize_wallet_deal(raw_deal: dict[str, Any], reader_session_id: str | None = None) -> WalletDealIn:
    session_id = reader_session_id or f"wallet-reader-{uuid4()}"
    captured_at = _to_datetime(raw_deal.get("captured_at")) or datetime.now(UTC)

    raw_payload = dict(raw_deal.get("raw_payload") or {})
    raw_payload.setdefault("ui_status", raw_deal.get("status"))
    raw_payload.setdefault("captured_at", captured_at.isoformat())
    raw_payload.setdefault("reader_session_id", session_id)

    notes = raw_deal.get("notes")
    if not notes:
        notes = "Imported from Wallet reader"

    return WalletDealIn(
        external_id=str(raw_deal["external_id"]),
        expected_fiat_amount=_to_decimal(raw_deal["expected_fiat_amount"]),
        fiat_currency=str(raw_deal.get("fiat_currency") or "RUB"),
        counterparty_name=raw_deal.get("counterparty_name"),
        counterparty_wallet_id=raw_deal.get("counterparty_wallet_id"),
        payment_method=raw_deal.get("payment_method"),
        opened_at=_to_datetime(raw_deal.get("opened_at")),
        completed_at=_to_datetime(raw_deal.get("completed_at")),
        status=map_status(raw_deal.get("status")),
        notes=notes,
        raw_payload=raw_payload,
    )


def normalize_wallet_batch(raw_deals: list[dict[str, Any]], reader_session_id: str | None = None) -> WalletDealBatchIn:
    session_id = reader_session_id or f"wallet-reader-{uuid4()}"
    return WalletDealBatchIn(
        deals=[normalize_wallet_deal(raw_deal, reader_session_id=session_id) for raw_deal in raw_deals]
    )
