from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BankTransaction, Deal, DealStatus, FiscalReceipt, ReceiptStatus, ReceiptType, TransactionDirection


RETURN_UI_STATUSES = {
    "refund",
    "refunded",
    "returned",
    "reversed",
    "chargeback",
    "return_detected",
}

REVIEW_UI_STATUSES = {
    "disputed",
    "third_party_review",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е")
    normalized = re.sub(r"[^0-9a-zа-я]+", " ", normalized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_counterparty_name(transaction: BankTransaction) -> str:
    raw_payload = transaction.raw_payload or {}
    transfer = raw_payload.get("rurTransfer") if isinstance(raw_payload, dict) else None
    if not isinstance(transfer, dict):
        transfer = {}

    if transaction.direction == TransactionDirection.outgoing:
        direct_value = transfer.get("payeeName") or transaction.payer_name
    else:
        direct_value = transaction.payer_name or transfer.get("payerName") or transfer.get("payeeName")

    text = str(direct_value or "").strip()
    if not text:
        return ""

    parts = [part.strip() for part in text.split("//") if part.strip()]
    for part in parts:
        normalized = _normalize_name(part)
        if not normalized:
            continue
        if any(token in normalized for token in ("сбербанк", "россия", "область", "республика", "улица", "проспект")):
            continue
        if sum(ch.isdigit() for ch in part) > 3:
            continue
        if len(normalized.split()) >= 2:
            return normalized

    return _normalize_name(text)


def _names_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True

    left_tokens = set(left.split())
    right_tokens = set(right.split())
    common_tokens = left_tokens & right_tokens
    return len(common_tokens) >= 2


def _reference_dt(deal: Deal) -> datetime:
    return deal.completed_at or deal.opened_at or deal.created_at


def _has_sale_receipt(deal: Deal) -> bool:
    return any(receipt.receipt_type == ReceiptType.sale for receipt in deal.receipts)


def _has_refund_receipt(deal: Deal) -> bool:
    return any(receipt.receipt_type == ReceiptType.refund for receipt in deal.receipts)


def _reset_non_return_status(deal: Deal) -> None:
    deal.return_reason = None
    deal.return_detected_at = None
    if _has_sale_receipt(deal):
        deal.status = DealStatus.receipted
    elif deal.bank_transactions:
        deal.status = DealStatus.matched
    else:
        deal.status = DealStatus.detected


def _has_bank_return_evidence(deal: Deal) -> bool:
    incoming_transactions = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.incoming]
    outgoing_transactions = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.outgoing]
    if not incoming_transactions or not outgoing_transactions:
        return False
    return True


def _is_return_reference(transaction: BankTransaction) -> bool:
    raw_payload = transaction.raw_payload or {}
    transfer = raw_payload.get("rurTransfer") if isinstance(raw_payload, dict) else None
    if not isinstance(transfer, dict):
        transfer = {}

    reference_text = " ".join(
        str(part or "").strip()
        for part in (
            transaction.reference,
            raw_payload.get("paymentPurpose") if isinstance(raw_payload, dict) else None,
            transfer.get("paymentPurpose"),
        )
        if str(part or "").strip()
    ).lower()
    return "возврат" in reference_text or "refund" in reference_text


def _move_refund_receipt_to_bank_return_deal(
    session: Session,
    *,
    target_deal: Deal,
    anchor_outgoing: BankTransaction,
) -> bool:
    existing_target_receipt = next(
        (receipt for receipt in target_deal.receipts if receipt.receipt_type == ReceiptType.refund),
        None,
    )
    if existing_target_receipt is not None:
        return False

    refund_receipts = session.scalars(
        select(FiscalReceipt).where(
            FiscalReceipt.receipt_type == ReceiptType.refund,
            FiscalReceipt.amount == target_deal.expected_fiat_amount,
            FiscalReceipt.issued_at >= anchor_outgoing.booked_at - timedelta(days=1),
            FiscalReceipt.issued_at <= anchor_outgoing.booked_at + timedelta(days=10),
        )
    ).all()
    if len(refund_receipts) != 1:
        return False

    refund_receipt = refund_receipts[0]
    if refund_receipt.deal_id == target_deal.id:
        return False

    source_deal = refund_receipt.deal
    if source_deal is not None and refund_receipt in source_deal.receipts:
        source_deal.receipts.remove(refund_receipt)
    if refund_receipt not in target_deal.receipts:
        target_deal.receipts.append(refund_receipt)

    if source_deal is not None and source_deal.id != target_deal.id:
        other_refunds = [
            receipt
            for receipt in source_deal.receipts
            if receipt.id != refund_receipt.id and receipt.receipt_type == ReceiptType.refund
        ]
        if not other_refunds:
            _reset_non_return_status(source_deal)
            classify_deal_status(source_deal)

    target_deal.status = DealStatus.return_detected
    target_deal.return_reason = target_deal.return_reason or "bank_return_with_refund_receipt"
    target_deal.return_detected_at = target_deal.return_detected_at or utc_now()
    classify_deal_status(target_deal)
    session.flush()
    return True


def _link_bank_returns_from_refund_receipts(session: Session) -> int:
    linked = 0
    deals = session.scalars(
        select(Deal).where(Deal.status == DealStatus.return_detected)
    ).all()

    for deal in deals:
        refund_receipts = [
            receipt
            for receipt in deal.receipts
            if receipt.receipt_type == ReceiptType.refund and receipt.status in {ReceiptStatus.issued, ReceiptStatus.pending}
        ]
        if not refund_receipts:
            continue

        if any(transaction.direction == TransactionDirection.outgoing for transaction in deal.bank_transactions):
            continue

        incoming_transactions = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.incoming]
        incoming_counterparty = ""
        if incoming_transactions:
            latest_incoming = max(incoming_transactions, key=lambda tx: tx.booked_at)
            incoming_counterparty = _extract_counterparty_name(latest_incoming)

        candidates: list[tuple[int, float, BankTransaction]] = []
        for receipt in refund_receipts:
            outgoing_transactions = session.scalars(
                select(BankTransaction).where(
                    BankTransaction.direction == TransactionDirection.outgoing,
                    BankTransaction.currency == deal.fiat_currency,
                    BankTransaction.amount == deal.expected_fiat_amount,
                    BankTransaction.deal_id.is_(None),
                    BankTransaction.booked_at >= receipt.issued_at - timedelta(days=3),
                    BankTransaction.booked_at <= receipt.issued_at + timedelta(days=7),
                )
            ).all()

            for outgoing in outgoing_transactions:
                outgoing_counterparty = _extract_counterparty_name(outgoing)
                name_priority = 1
                if incoming_counterparty and outgoing_counterparty and _names_match(incoming_counterparty, outgoing_counterparty):
                    name_priority = 0
                delta_seconds = abs((outgoing.booked_at - receipt.issued_at).total_seconds())
                candidates.append((name_priority, delta_seconds, outgoing))

        if not candidates:
            continue

        candidates.sort(key=lambda item: item[:2])
        best_priority, best_delta, best_outgoing = candidates[0]
        if len(candidates) > 1 and candidates[1][:2] == (best_priority, best_delta):
            continue

        best_outgoing.deal_id = deal.id
        deal.return_reason = deal.return_reason or "refund_receipt_with_bank_outgoing"
        deal.return_detected_at = deal.return_detected_at or utc_now()
        linked += 1

    if linked:
        session.commit()
    return linked


def _link_explicit_bank_return_pairs(session: Session) -> int:
    linked = 0
    outgoing_transactions = session.scalars(
        select(BankTransaction).where(
            BankTransaction.direction == TransactionDirection.outgoing,
            BankTransaction.currency == "RUB",
            BankTransaction.deal_id.is_(None),
        )
    ).all()

    for outgoing in outgoing_transactions:
        if not _is_return_reference(outgoing):
            continue

        outgoing_counterparty = _extract_counterparty_name(outgoing)
        if not outgoing_counterparty:
            continue

        incoming_candidates = session.scalars(
            select(BankTransaction).where(
                BankTransaction.direction == TransactionDirection.incoming,
                BankTransaction.currency == outgoing.currency,
                BankTransaction.amount == outgoing.amount,
                BankTransaction.booked_at <= outgoing.booked_at,
                BankTransaction.booked_at >= outgoing.booked_at - timedelta(days=3),
            )
        ).all()
        incoming_candidates = [
            tx
            for tx in incoming_candidates
            if tx.deal_id is None and _names_match(_extract_counterparty_name(tx), outgoing_counterparty)
        ]
        if not incoming_candidates:
            continue

        incoming_candidates.sort(key=lambda tx: abs((outgoing.booked_at - tx.booked_at).total_seconds()))
        paired_incoming = incoming_candidates[0]
        if len(incoming_candidates) > 1:
            first_delta = abs((outgoing.booked_at - incoming_candidates[0].booked_at).total_seconds())
            second_delta = abs((outgoing.booked_at - incoming_candidates[1].booked_at).total_seconds())
            if first_delta == second_delta:
                continue

        deal_candidates = session.scalars(
            select(Deal).where(
                Deal.source == "wallet",
                Deal.expected_fiat_amount == outgoing.amount,
                Deal.fiat_currency == outgoing.currency,
                Deal.status != DealStatus.canceled,
                Deal.completed_at.is_not(None),
                Deal.completed_at <= paired_incoming.booked_at,
            )
        ).all()
        if not deal_candidates:
            continue

        ranked_candidates: list[tuple[int, int, float, Deal]] = []
        for deal in deal_candidates:
            linked_incomings = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.incoming]
            linked_outgoings = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.outgoing]

            if linked_outgoings:
                continue

            incoming_name_priority = 1
            if linked_incomings:
                latest_linked_incoming = max(linked_incomings, key=lambda tx: tx.booked_at)
                if _names_match(_extract_counterparty_name(latest_linked_incoming), outgoing_counterparty):
                    incoming_name_priority = 0
                else:
                    incoming_name_priority = 2

            bank_link_priority = 0 if not deal.bank_transactions else 1
            delta_seconds = abs((_reference_dt(deal) - paired_incoming.booked_at).total_seconds())
            ranked_candidates.append((incoming_name_priority, bank_link_priority, delta_seconds, deal))

        if not ranked_candidates:
            continue

        ranked_candidates.sort(key=lambda item: item[:3])
        best_rank = ranked_candidates[0][:3]
        best_deals = [deal for *rank, deal in ranked_candidates if tuple(rank) == best_rank]
        if len(best_deals) != 1:
            continue

        target_deal = best_deals[0]
        paired_incoming.deal_id = target_deal.id
        outgoing.deal_id = target_deal.id
        target_deal.status = DealStatus.return_detected
        target_deal.return_reason = "bank_return_same_counterparty"
        target_deal.return_detected_at = target_deal.return_detected_at or utc_now()
        session.flush()
        _move_refund_receipt_to_bank_return_deal(session, target_deal=target_deal, anchor_outgoing=outgoing)
        classify_deal_status(target_deal)
        linked += 1

    if linked:
        session.commit()
    return linked


def link_bank_return_transactions(session: Session) -> int:
    linked = _link_explicit_bank_return_pairs(session)
    linked += _link_bank_returns_from_refund_receipts(session)

    outgoing_transactions = session.scalars(
        select(BankTransaction).where(
            BankTransaction.direction == TransactionDirection.outgoing,
            BankTransaction.currency == "RUB",
            BankTransaction.deal_id.is_(None),
        )
    ).all()

    for outgoing in outgoing_transactions:
        outgoing_counterparty = _extract_counterparty_name(outgoing)
        if not outgoing_counterparty:
            continue

        candidates: list[tuple[int, float, Deal]] = []
        deals = session.scalars(
            select(Deal).where(
                Deal.expected_fiat_amount == outgoing.amount,
                Deal.fiat_currency == outgoing.currency,
                Deal.completed_at.is_not(None),
                Deal.completed_at <= outgoing.booked_at,
                Deal.status.in_([DealStatus.matched, DealStatus.receipted, DealStatus.return_detected]),
            )
        ).all()

        for deal in deals:
            incoming_transactions = [tx for tx in deal.bank_transactions if tx.direction == TransactionDirection.incoming]
            if not incoming_transactions:
                continue

            latest_incoming = max(incoming_transactions, key=lambda tx: tx.booked_at)
            if outgoing.booked_at < latest_incoming.booked_at:
                continue
            if outgoing.booked_at - latest_incoming.booked_at > timedelta(days=14):
                continue

            incoming_counterparty = _extract_counterparty_name(latest_incoming)
            if not _names_match(incoming_counterparty, outgoing_counterparty):
                continue

            has_refund_receipt = any(receipt.receipt_type == ReceiptType.refund for receipt in deal.receipts)
            receipt_priority = 0 if has_refund_receipt else 1
            delta_seconds = abs((outgoing.booked_at - latest_incoming.booked_at).total_seconds())
            candidates.append((receipt_priority, delta_seconds, deal))

        if not candidates:
            continue

        candidates.sort(key=lambda item: item[:2])
        best_priority, best_delta, best_deal = candidates[0]
        if len(candidates) > 1 and candidates[1][:2] == (best_priority, best_delta):
            continue

        outgoing.deal_id = best_deal.id
        best_deal.status = DealStatus.return_detected
        best_deal.return_reason = best_deal.return_reason or "bank_return_same_counterparty"
        best_deal.return_detected_at = best_deal.return_detected_at or utc_now()
        classify_deal_status(best_deal)
        linked += 1

    if linked:
        session.commit()
    return linked


def classify_deal_status(deal: Deal) -> DealStatus:
    raw_payload = deal.raw_payload or {}
    ui_status = str(raw_payload.get("ui_status") or "").strip().lower()

    deal.return_reason = None
    deal.return_detected_at = None

    if any(
        receipt.receipt_type == ReceiptType.refund and receipt.status in {ReceiptStatus.issued, ReceiptStatus.pending}
        for receipt in deal.receipts
    ):
        deal.status = DealStatus.return_detected
        deal.return_reason = "refund_receipt"
        deal.return_detected_at = deal.return_detected_at or utc_now()
        return deal.status

    if raw_payload.get("is_return") or raw_payload.get("return_detected") or raw_payload.get("refund_detected"):
        deal.status = DealStatus.return_detected
        deal.return_reason = str(raw_payload.get("return_reason") or "payload_flag")
        deal.return_detected_at = deal.return_detected_at or utc_now()
        return deal.status

    if _has_bank_return_evidence(deal):
        deal.status = DealStatus.return_detected
        deal.return_reason = "bank_return_linked"
        deal.return_detected_at = deal.return_detected_at or utc_now()
        return deal.status

    if ui_status in RETURN_UI_STATUSES:
        deal.status = DealStatus.return_detected
        deal.return_reason = str(raw_payload.get("return_reason") or ui_status)
        deal.return_detected_at = deal.return_detected_at or utc_now()
        return deal.status

    if ui_status in REVIEW_UI_STATUSES and deal.status != DealStatus.return_detected:
        deal.status = DealStatus.third_party_review
        return deal.status

    sale_receipt_exists = any(
        receipt.receipt_type == ReceiptType.sale and receipt.status == ReceiptStatus.issued for receipt in deal.receipts
    )

    if deal.bank_transactions:
        deal.status = DealStatus.receipted if sale_receipt_exists else DealStatus.matched
    elif sale_receipt_exists:
        deal.status = DealStatus.receipted
    else:
        deal.status = DealStatus.detected

    return deal.status


def classify_all_deals(session: Session) -> int:
    deals = session.scalars(select(Deal)).all()
    for deal in deals:
        classify_deal_status(deal)
    session.commit()
    return len(deals)
