from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from math import inf

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deal, DealStatus, FiscalReceipt, ReceiptStatus, ReceiptType, TransactionDirection
from app.services.classification import classify_deal_status


def _reference_dt(deal: Deal):
    return deal.completed_at or deal.opened_at


def _to_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).replace("\u00a0", " ").replace(" ", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _extract_receipt_trade_signature(receipt: FiscalReceipt) -> tuple[Decimal | None, Decimal | None]:
    raw_payload = receipt.raw_payload or {}
    document = raw_payload.get("document") if isinstance(raw_payload, dict) else None
    if not isinstance(document, dict):
        return None, None

    items = document.get("items")
    if not isinstance(items, list) or not items:
        return None, None

    item = items[0] if isinstance(items[0], dict) else None
    if not item:
        return None, None

    count = _to_decimal(item.get("count"))
    price = _to_decimal(item.get("price"))
    if price is not None and price > Decimal("1000"):
        price = price / Decimal("100")
    return price, count


def _candidate_rank(receipt: FiscalReceipt, deal: Deal) -> tuple[int, float, float, int, float]:
    receipt_price, receipt_count = _extract_receipt_trade_signature(receipt)
    raw_payload = deal.raw_payload or {}
    deal_price = _to_decimal(raw_payload.get("price"))
    deal_count = _to_decimal(raw_payload.get("net_crypto_amount"))

    if receipt_price is not None and deal_price is not None:
        price_diff = float(abs(deal_price - receipt_price))
    else:
        price_diff = inf

    if receipt_count is not None and deal_count is not None:
        count_diff = float(abs(deal_count - receipt_count))
    else:
        count_diff = inf

    if price_diff <= 0.02 and count_diff <= 0.01:
        signature_priority = 0
    elif price_diff <= 0.02 or count_diff <= 0.01:
        signature_priority = 1
    else:
        signature_priority = 2

    reference_dt = _reference_dt(deal)
    delta_seconds = abs((reference_dt - receipt.issued_at).total_seconds()) if reference_dt else inf
    status_priority = 0 if deal.status in {DealStatus.matched, DealStatus.receipted, DealStatus.return_detected} else 1
    outgoing_priority = 0 if any(tx.direction == TransactionDirection.outgoing for tx in deal.bank_transactions) else 1
    return (outgoing_priority, signature_priority, price_diff, count_diff, status_priority, delta_seconds)


def reconcile_receipts(session: Session, window_hours: int = 24) -> int:
    receipts = session.scalars(select(FiscalReceipt).where(FiscalReceipt.deal_id.is_(None))).all()
    linked = 0
    for receipt in receipts:
        query = select(Deal).where(Deal.fiat_currency == "RUB", Deal.expected_fiat_amount == receipt.amount)
        if receipt.receipt_type == ReceiptType.refund:
            query = query.where(
                Deal.completed_at.is_not(None),
                Deal.status != DealStatus.canceled,
            ).order_by(Deal.completed_at.desc())
        else:
            window = timedelta(hours=window_hours)
            query = query.where(
                Deal.opened_at.is_not(None),
                Deal.opened_at >= receipt.issued_at - window,
                Deal.opened_at <= receipt.issued_at + window,
            ).order_by(Deal.opened_at.asc())

        candidates = session.scalars(query).all()
        if not candidates:
            continue

        ranked_candidates = sorted(candidates, key=lambda deal: _candidate_rank(receipt, deal))
        best_candidate = ranked_candidates[0]
        if len(ranked_candidates) > 1 and _candidate_rank(receipt, ranked_candidates[1]) == _candidate_rank(
            receipt, best_candidate
        ):
            continue

        candidates = [best_candidate]
        for deal in candidates:
            if receipt.receipt_type == ReceiptType.sale and deal.status not in {
                DealStatus.canceled,
                DealStatus.return_detected,
            }:
                receipt.deal_id = deal.id
                deal.status = DealStatus.receipted if receipt.status == ReceiptStatus.issued else deal.status
                classify_deal_status(deal)
                linked += 1
                break

            if receipt.receipt_type == ReceiptType.refund:
                receipt.deal_id = deal.id
                deal.status = DealStatus.return_detected
                deal.return_reason = deal.return_reason or "refund_receipt_auto_match"
                classify_deal_status(deal)
                linked += 1
                break

    session.commit()
    return linked
