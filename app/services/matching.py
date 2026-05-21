from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from itertools import combinations
import re

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BankTransaction, Deal, DealStatus, MatchDecision, TransactionDirection
from app.services.classification import classify_deal_status
from app.schemas import MatchRunResult


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def wallet_side(deal: Deal) -> str | None:
    raw_payload = deal.raw_payload or {}
    explicit_side = str(raw_payload.get("side") or "").strip().lower()
    if explicit_side in {"buy", "sell"}:
        return explicit_side

    ad_type = str(raw_payload.get("ad_type") or "").strip().lower()
    role = str(raw_payload.get("role") or "").strip().lower()
    combined = f"{ad_type} {role}"
    if any(token in combined for token in ("buy", "purchase", "buyer", "покуп")):
        return "buy"
    if any(token in combined for token in ("sell", "sale", "seller", "прод")):
        return "sell"
    return None


def expected_bank_direction(deal: Deal) -> TransactionDirection:
    side = wallet_side(deal)
    if side == "buy":
        return TransactionDirection.outgoing
    return TransactionDirection.incoming


def reference_timestamp(deal: Deal):
    return deal.completed_at or deal.opened_at


def reference_tokens(deal: Deal) -> list[str]:
    raw_payload = deal.raw_payload or {}
    tokens = [str(deal.external_id or "").strip()]
    ad_number = str(raw_payload.get("ad_number") or "").strip()
    if ad_number:
        tokens.append(ad_number)
    return [token.lower() for token in tokens if token]


def reference_text(transaction: BankTransaction) -> str:
    parts = [
        transaction.reference or "",
        str((transaction.raw_payload or {}).get("paymentPurpose") or ""),
        str((transaction.raw_payload or {}).get("number") or ""),
    ]
    return " ".join(part.strip().lower() for part in parts if part).strip()


def _transaction_priority_for_deal(deal: Deal, transaction: BankTransaction) -> tuple[int, int]:
    transaction_reference = reference_text(transaction)
    reference_match = any(token in transaction_reference for token in reference_tokens(deal))
    delta_minutes = 10**9
    deal_reference_dt = reference_timestamp(deal)
    if deal_reference_dt is not None:
        delta_minutes = int(abs((transaction.booked_at - deal_reference_dt).total_seconds()) // 60)
    return (0 if reference_match else 1, delta_minutes)


def deduplicate_linked_transactions(session: Session) -> int:
    deals = session.scalars(select(Deal)).all()
    unlinked = 0
    for deal in deals:
        for direction in TransactionDirection:
            linked = [tx for tx in deal.bank_transactions if tx.direction == direction]
            if len(linked) <= 1:
                continue
            linked.sort(key=lambda tx: _transaction_priority_for_deal(deal, tx))
            for duplicate in linked[1:]:
                duplicate.deal_id = None
                unlinked += 1
    if unlinked:
        session.commit()
    return unlinked


def _candidate_transactions_query(deal: Deal) -> Select[tuple[BankTransaction]]:
    amount_tolerance = Decimal(str(settings.match_amount_tolerance))
    min_amount = deal.expected_fiat_amount - amount_tolerance
    max_amount = deal.expected_fiat_amount + amount_tolerance
    query = select(BankTransaction).where(
        BankTransaction.deal_id.is_(None),
        BankTransaction.currency == deal.fiat_currency,
        BankTransaction.direction == expected_bank_direction(deal),
        BankTransaction.amount >= min_amount,
        BankTransaction.amount <= max_amount,
    )
    deal_reference_dt = reference_timestamp(deal)
    if deal_reference_dt:
        window = timedelta(hours=settings.match_window_hours)
        query = query.where(
            BankTransaction.booked_at >= deal_reference_dt - window,
            BankTransaction.booked_at <= deal_reference_dt + window,
        )
    return query.order_by(BankTransaction.booked_at.asc())


def _split_candidate_transactions_query(deal: Deal) -> Select[tuple[BankTransaction]]:
    deal_reference_dt = reference_timestamp(deal)
    query = select(BankTransaction).where(
        BankTransaction.deal_id.is_(None),
        BankTransaction.currency == deal.fiat_currency,
        BankTransaction.direction == expected_bank_direction(deal),
        BankTransaction.amount > 0,
        BankTransaction.amount < deal.expected_fiat_amount,
    )
    if deal_reference_dt:
        window = timedelta(hours=settings.match_window_hours)
        query = query.where(
            BankTransaction.booked_at >= deal_reference_dt - window,
            BankTransaction.booked_at <= deal_reference_dt + window,
        )
    return query.order_by(BankTransaction.booked_at.asc())


def score_candidate(deal: Deal, transaction: BankTransaction) -> tuple[int, dict]:
    score = 0
    rule_hits: dict[str, object] = {
        "amount_exact": False,
        "name_match": False,
        "same_day_window": False,
        "reference_order_match": False,
        "third_party_risk": False,
        "has_counterparty_name": bool(deal.counterparty_name),
        "delta_minutes": None,
    }

    if transaction.amount == deal.expected_fiat_amount:
        score += 70
        rule_hits["amount_exact"] = True

    deal_reference_dt = reference_timestamp(deal)
    if deal_reference_dt:
        delta = abs(transaction.booked_at - deal_reference_dt)
        delta_minutes = int(delta.total_seconds() // 60)
        rule_hits["delta_minutes"] = delta_minutes
        if delta <= timedelta(hours=24):
            rule_hits["same_day_window"] = True
            score += max(1, 25 - min(delta_minutes // 30, 24))

    transaction_reference = reference_text(transaction)
    if transaction_reference:
        if any(token in transaction_reference for token in reference_tokens(deal)):
            score += 50
            rule_hits["reference_order_match"] = True

    deal_name = normalize_name(deal.counterparty_name)
    payer_name = normalize_name(transaction.payer_name)
    if deal_name and payer_name:
        if deal_name in payer_name or payer_name in deal_name:
            score += 10
            rule_hits["name_match"] = True
        else:
            rule_hits["third_party_risk"] = True

    return score, rule_hits


def _match_split_transactions(session: Session, deal: Deal) -> MatchRunResult | None:
    amount_tolerance = Decimal(str(settings.match_amount_tolerance))
    candidates = session.scalars(_split_candidate_transactions_query(deal)).all()
    if len(candidates) < 2:
        return None

    limited_candidates = candidates[:8]
    best_combo: tuple[BankTransaction, ...] | None = None
    best_rules: dict[str, object] = {}
    best_score = -1

    for combo_size in (2, 3):
        for combo in combinations(limited_candidates, combo_size):
            total_amount = sum((transaction.amount for transaction in combo), Decimal("0"))
            if abs(total_amount - deal.expected_fiat_amount) > amount_tolerance:
                continue

            deal_reference_dt = reference_timestamp(deal)
            delta_minutes_values: list[int] = []
            if deal_reference_dt is not None:
                for transaction in combo:
                    delta_minutes_values.append(int(abs((transaction.booked_at - deal_reference_dt).total_seconds()) // 60))

            combo_reference = " ".join(reference_text(transaction) for transaction in combo)
            reference_match = any(token in combo_reference for token in reference_tokens(deal))

            normalized_names = {normalize_name(transaction.payer_name) for transaction in combo if normalize_name(transaction.payer_name)}
            shared_name = len(normalized_names) == 1 and len(combo) > 1

            score = 85
            if delta_minutes_values:
                score += max(0, 10 - min(delta_minutes_values) // 30)
            if reference_match:
                score += 10
            if shared_name:
                score += 5

            rules = {
                "split_match": True,
                "component_count": len(combo),
                "component_amounts": [str(transaction.amount) for transaction in combo],
                "component_transaction_ids": [transaction.external_id for transaction in combo],
                "total_amount": str(total_amount),
                "reference_order_match": reference_match,
                "shared_name": shared_name,
                "delta_minutes": min(delta_minutes_values) if delta_minutes_values else None,
            }

            if score > best_score:
                best_score = score
                best_combo = combo
                best_rules = rules

    if best_combo is None:
        return None

    for transaction in best_combo:
        transaction.deal_id = deal.id
        session.add(
            MatchDecision(
                deal_id=deal.id,
                bank_transaction_id=transaction.id,
                score=best_score,
                outcome="matched_split_sum",
                rule_hits=best_rules,
            )
        )

    deal.status = DealStatus.matched
    classify_deal_status(deal)
    primary_transaction = min(best_combo, key=lambda item: abs((item.booked_at - (reference_timestamp(deal) or item.booked_at)).total_seconds()))
    return MatchRunResult(
        deal_external_id=deal.external_id,
        bank_transaction_external_id=primary_transaction.external_id,
        score=best_score,
        outcome="matched_split_sum",
        rule_hits=best_rules,
    )


def _resolve_ambiguous_groups(session: Session, deals: list[Deal]) -> list[MatchRunResult]:
    grouped: dict[tuple, list[Deal]] = {}
    for deal in deals:
        if deal.bank_transactions:
            continue
        deal_reference_dt = reference_timestamp(deal)
        if deal_reference_dt is None:
            continue
        key = (
            deal.expected_fiat_amount,
            deal.fiat_currency,
            expected_bank_direction(deal).value,
            deal_reference_dt.date().isoformat(),
        )
        grouped.setdefault(key, []).append(deal)

    results: list[MatchRunResult] = []
    for group_deals in grouped.values():
        candidate_map: dict[str, dict[str, int]] = {}
        transaction_index: dict[str, BankTransaction] = {}
        for deal in group_deals:
            deal_reference_dt = reference_timestamp(deal)
            if deal_reference_dt is None:
                continue
            deltas: dict[str, int] = {}
            for transaction in session.scalars(_candidate_transactions_query(deal)).all():
                if transaction.deal_id is not None:
                    continue
                delta_minutes = int(abs((transaction.booked_at - deal_reference_dt).total_seconds()) // 60)
                deltas[transaction.id] = delta_minutes
                transaction_index[transaction.id] = transaction
            if deltas:
                candidate_map[deal.id] = deltas

        if not candidate_map or len(candidate_map) != len(group_deals):
            continue

        deals_sorted = sorted(group_deals, key=lambda deal: reference_timestamp(deal) or deal.created_at)
        transactions_sorted = sorted(transaction_index.values(), key=lambda transaction: transaction.booked_at)
        if len(transactions_sorted) < len(deals_sorted) or len(transactions_sorted) > 12:
            continue

        large_cost = 10**9
        cost_matrix = [
            [candidate_map[deal.id].get(transaction.id, large_cost) for transaction in transactions_sorted]
            for deal in deals_sorted
        ]
        deal_count = len(deals_sorted)
        transaction_count = len(transactions_sorted)

        @lru_cache(maxsize=None)
        def solve(deal_idx: int, tx_idx: int) -> tuple[int, tuple[int, ...]]:
            if deal_idx == deal_count:
                return 0, ()
            if tx_idx == transaction_count:
                return large_cost, ()

            best_cost, best_path = solve(deal_idx, tx_idx + 1)
            current_cost = cost_matrix[deal_idx][tx_idx]
            if current_cost < large_cost:
                next_cost, next_path = solve(deal_idx + 1, tx_idx + 1)
                total_cost = current_cost + next_cost
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_path = (tx_idx,) + next_path
            return best_cost, best_path

        total_cost, assignment = solve(0, 0)
        if total_cost >= large_cost or len(assignment) != deal_count:
            continue

        for deal, tx_idx in zip(deals_sorted, assignment):
            transaction = transactions_sorted[tx_idx]
            transaction.deal_id = deal.id
            deal.status = DealStatus.matched
            classify_deal_status(deal)
            rule_hits = {
                "cluster_match": True,
                "cluster_size": deal_count,
                "ordered_timeline": True,
                "delta_minutes": cost_matrix[deals_sorted.index(deal)][tx_idx],
            }
            session.add(
                MatchDecision(
                    deal_id=deal.id,
                    bank_transaction_id=transaction.id,
                    score=85,
                    outcome="matched_ordered_cluster",
                    rule_hits=rule_hits,
                )
            )
            results.append(
                MatchRunResult(
                    deal_external_id=deal.external_id,
                    bank_transaction_external_id=transaction.external_id,
                    score=85,
                    outcome="matched_ordered_cluster",
                    rule_hits=rule_hits,
                )
            )

    return results


def run_matching(session: Session) -> list[MatchRunResult]:
    deduplicate_linked_transactions(session)

    deals = session.scalars(
        select(Deal).where(
            Deal.status.in_(
                [
                    DealStatus.detected,
                    DealStatus.awaiting_payment,
                    DealStatus.matched,
                    DealStatus.receipted,
                    DealStatus.third_party_review,
                    DealStatus.return_detected,
                ]
            )
        )
    ).all()
    deals = [
        deal
        for deal in deals
        if not any(transaction.direction == expected_bank_direction(deal) for transaction in deal.bank_transactions)
    ]

    results_by_deal: dict[str, MatchRunResult] = {}
    ambiguous_deals: list[Deal] = []
    for deal in deals:
        candidates = session.scalars(_candidate_transactions_query(deal)).all()
        if not candidates:
            split_result = _match_split_transactions(session, deal)
            if split_result is not None:
                results_by_deal[deal.external_id] = split_result
            else:
                results_by_deal[deal.external_id] = MatchRunResult(
                    deal_external_id=deal.external_id,
                    score=0,
                    outcome="no_candidate",
                    rule_hits={},
                )
            continue

        best_transaction: BankTransaction | None = None
        best_score = -1
        best_rules: dict = {}
        scored_candidates: list[tuple[BankTransaction, int, dict]] = []
        for candidate in candidates:
            score, rule_hits = score_candidate(deal, candidate)
            scored_candidates.append((candidate, score, rule_hits))
            if score > best_score:
                best_score = score
                best_transaction = candidate
                best_rules = rule_hits

        if best_transaction is None:
            continue

        resolved_by_closest_time = False
        top_candidates = [(candidate, score, rules) for candidate, score, rules in scored_candidates if score == best_score]
        if len(top_candidates) > 1:
            top_candidates.sort(key=lambda item: item[2].get("delta_minutes") if item[2].get("delta_minutes") is not None else 10**9)
            first_delta = top_candidates[0][2].get("delta_minutes")
            second_delta = top_candidates[1][2].get("delta_minutes")
            if first_delta is not None and second_delta is not None and first_delta + 15 < second_delta:
                best_transaction, best_score, best_rules = top_candidates[0]
                resolved_by_closest_time = True

        best_score_count = sum(1 for _, score, _ in scored_candidates if score == best_score)
        best_rules["candidate_count"] = len(scored_candidates)
        best_rules["best_score_count"] = best_score_count
        best_rules["resolved_by_closest_time"] = resolved_by_closest_time
        ambiguous_amount_only = (
            best_score >= 70
            and best_score_count > 1
            and not best_rules.get("name_match")
            and not best_rules.get("reference_order_match")
            and not resolved_by_closest_time
        )

        if best_rules.get("third_party_risk"):
            deal.status = DealStatus.third_party_review
            outcome = "third_party_risk"
        elif ambiguous_amount_only:
            split_result = _match_split_transactions(session, deal)
            if split_result is not None:
                results_by_deal[deal.external_id] = split_result
                continue
            outcome = "ambiguous_amount_only"
            ambiguous_deals.append(deal)
        elif best_score >= 70:
            best_transaction.deal_id = deal.id
            deal.status = DealStatus.matched
            outcome = "matched"
        else:
            split_result = _match_split_transactions(session, deal)
            if split_result is not None:
                results_by_deal[deal.external_id] = split_result
                continue
            outcome = "weak_match"

        classify_deal_status(deal)

        session.add(
            MatchDecision(
                deal_id=deal.id,
                bank_transaction_id=best_transaction.id,
                score=best_score,
                outcome=outcome,
                rule_hits=best_rules,
            )
        )

        results_by_deal[deal.external_id] = MatchRunResult(
            deal_external_id=deal.external_id,
            bank_transaction_external_id=best_transaction.external_id,
            score=best_score,
            outcome=outcome,
            rule_hits=best_rules,
        )

    for resolved in _resolve_ambiguous_groups(session, ambiguous_deals):
        results_by_deal[resolved.deal_external_id] = resolved

    session.commit()
    return list(results_by_deal.values())
