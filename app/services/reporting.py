from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import os
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BankTransaction, Deal, DealStatus, FiscalReceipt, ReceiptType, TransactionDirection
from app.schemas import (
    DealOut,
    ReconciliationSummary,
    TelegramDashboardSnapshot,
    WalletProfitStats,
    WalletRateStats,
    WalletTradingStats,
)


CSV_RANGE_RE = re.compile(r"p2p-order-history_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)
TRADED_DEAL_SOURCES = ("wallet", "manual")


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


def _fee_adjusted_crypto_amount(raw_payload: dict, side: str | None) -> Decimal | None:
    net_crypto_amount = _to_decimal(raw_payload.get("net_crypto_amount"))
    if net_crypto_amount is None or net_crypto_amount <= 0:
        return None
    return net_crypto_amount


def _listed_trade_crypto_amount(raw_payload: dict, fallback_amount: Decimal) -> Decimal:
    price = _to_decimal(raw_payload.get("price"))
    fiat_amount = _to_decimal(raw_payload.get("fiat_amount"))
    if price is not None and price > 0 and fiat_amount is not None and fiat_amount > 0:
        return fiat_amount / price
    return fallback_amount


def _wallet_side(raw_payload: dict) -> str | None:
    explicit_side = str(raw_payload.get("side") or "").strip().lower()
    if explicit_side in {"buy", "sell"}:
        return explicit_side

    ad_type = str(raw_payload.get("ad_type") or "").strip().lower()
    role = str(raw_payload.get("role") or "").strip().lower()
    combined = f"{ad_type} {role}"
    if any(token in combined for token in ("buy", "purchase", "buyer", "?????")):
        return "buy"
    if any(token in combined for token in ("sell", "sale", "seller", "????")):
        return "sell"
    return None


def _deal_period_filters(date_from: date | None, date_to: date | None) -> list:
    filters = []
    if date_from is not None:
        filters.append(func.date(func.coalesce(Deal.opened_at, Deal.created_at)) >= date_from.isoformat())
    if date_to is not None:
        filters.append(func.date(func.coalesce(Deal.opened_at, Deal.created_at)) <= date_to.isoformat())
    return filters


def _bank_period_filters(date_from: date | None, date_to: date | None) -> list:
    filters = []
    if date_from is not None:
        filters.append(func.date(BankTransaction.booked_at) >= date_from.isoformat())
    if date_to is not None:
        filters.append(func.date(BankTransaction.booked_at) <= date_to.isoformat())
    return filters


def _receipt_period_filters(date_from: date | None, date_to: date | None) -> list:
    filters = []
    if date_from is not None:
        filters.append(func.date(FiscalReceipt.issued_at) >= date_from.isoformat())
    if date_to is not None:
        filters.append(func.date(FiscalReceipt.issued_at) <= date_to.isoformat())
    return filters


def build_reconciliation_summary(
    session: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ReconciliationSummary:
    deal_filters = _deal_period_filters(date_from, date_to)
    bank_filters = _bank_period_filters(date_from, date_to)

    total_deals = session.scalar(select(func.count()).select_from(Deal).where(*deal_filters)) or 0
    matched_deals = session.scalar(
        select(func.count()).select_from(Deal).where(
            Deal.bank_transactions.any(),
            *deal_filters,
        )
    ) or 0
    receipted_deals = session.scalar(
        select(func.count()).select_from(Deal).where(Deal.status == DealStatus.receipted, *deal_filters)
    ) or 0
    return_detected_deals = session.scalar(
        select(func.count()).select_from(Deal).where(Deal.status == DealStatus.return_detected, *deal_filters)
    ) or 0
    third_party_review_deals = session.scalar(
        select(func.count()).select_from(Deal).where(Deal.status == DealStatus.third_party_review, *deal_filters)
    ) or 0
    unmatched_bank_transactions = session.scalar(
        select(func.count()).select_from(BankTransaction).where(BankTransaction.deal_id.is_(None), *bank_filters)
    ) or 0

    total_expected_rub = session.scalar(
        select(func.coalesce(func.sum(Deal.expected_fiat_amount), 0)).where(Deal.fiat_currency == "RUB", *deal_filters)
    )
    total_received_rub = session.scalar(
        select(func.coalesce(func.sum(BankTransaction.amount), 0)).where(
            BankTransaction.currency == "RUB",
            BankTransaction.direction == TransactionDirection.incoming,
            *bank_filters,
        )
    )
    total_returned_rub = session.scalar(
        select(func.coalesce(func.sum(Deal.expected_fiat_amount), 0)).where(
            Deal.fiat_currency == "RUB",
            Deal.status == DealStatus.return_detected,
            *deal_filters,
        )
    )

    return ReconciliationSummary(
        total_deals=total_deals,
        matched_deals=matched_deals,
        receipted_deals=receipted_deals,
        return_detected_deals=return_detected_deals,
        third_party_review_deals=third_party_review_deals,
        unmatched_bank_transactions=unmatched_bank_transactions,
        total_expected_rub=Decimal(total_expected_rub or 0),
        total_received_rub=Decimal(total_received_rub or 0),
        total_returned_rub=Decimal(total_returned_rub or 0),
    )


def _wallet_csv_coverage(
    session: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[bool, str, list[str], list[date]]:
    period_filters = _deal_period_filters(date_from, date_to) if date_from or date_to else []
    wallet_deal_count = session.scalar(
        select(func.count()).select_from(Deal).where(
            Deal.source.in_(TRADED_DEAL_SOURCES),
            *period_filters,
        )
    ) or 0
    if wallet_deal_count == 0:
        return True, "В выбранном периоде нет сделок из TG Wallet.", [], []

    source_files = set()
    for raw_payload in session.scalars(select(Deal.raw_payload).where(Deal.source == "wallet")):
        if not isinstance(raw_payload, dict):
            continue
        source_file = raw_payload.get("source_file")
        if source_file:
            source_files.add(str(source_file))

    files = sorted(os.path.basename(path) for path in source_files)
    if not files:
        return (
            False,
            "За этот период нет загруженных CSV-файлов из TG Wallet. Пожалуйста, выгрузите CSV и загрузите его в систему.",
            [],
            [date_from] if date_from else [],
        )

    if date_from is None or date_to is None:
        return True, "CSV из TG Wallet загружены.", files, []

    covered_dates: set[date] = set()
    matched_files: list[str] = []
    for filename in files:
        match = CSV_RANGE_RE.search(filename)
        if not match:
            continue
        file_from = date.fromisoformat(match.group(1))
        file_to = date.fromisoformat(match.group(2))
        if file_to < date_from or file_from > date_to:
            continue
        matched_files.append(filename)
        cursor = max(file_from, date_from)
        last = min(file_to, date_to)
        while cursor <= last:
            covered_dates.add(cursor)
            cursor += timedelta(days=1)

    expected_dates: list[date] = []
    cursor = date_from
    while cursor <= date_to:
        expected_dates.append(cursor)
        cursor += timedelta(days=1)

    missing_dates = [item for item in expected_dates if item not in covered_dates]
    if missing_dates:
        preview = ", ".join(item.isoformat() for item in missing_dates[:5])
        suffix = " ..." if len(missing_dates) > 5 else ""
        return (
            False,
            f"За выбранный период не хватает CSV из TG Wallet. Нужна выгрузка за даты: {preview}{suffix}",
            matched_files,
            missing_dates,
        )

    return True, "CSV из TG Wallet покрывают выбранный период.", matched_files, []


def _wallet_csv_coverage_v2(
    session: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[bool, str, list[str], list[date]]:
    period_filters = _deal_period_filters(date_from, date_to) if date_from or date_to else []
    wallet_deal_count = session.scalar(
        select(func.count()).select_from(Deal).where(
            Deal.source == "wallet",
            *period_filters,
        )
    ) or 0
    if wallet_deal_count == 0:
        return True, "В выбранном периоде нет сделок из TG Wallet.", [], []

    source_files = set()
    for raw_payload in session.scalars(select(Deal.raw_payload).where(Deal.source == "wallet")):
        if not isinstance(raw_payload, dict):
            continue
        source_file = raw_payload.get("source_file")
        if source_file:
            source_files.add(str(source_file))

    files = sorted(os.path.basename(path) for path in source_files)
    if not files:
        return (
            False,
            "За этот период нет загруженных CSV-файлов из TG Wallet. Пожалуйста, выгрузите CSV и загрузите его в систему.",
            [],
            [date_from] if date_from else [],
        )

    parsed_ranges: list[tuple[str, date, date]] = []
    for filename in files:
        match = CSV_RANGE_RE.search(filename)
        if not match:
            continue
        parsed_ranges.append(
            (
                filename,
                date.fromisoformat(match.group(1)),
                date.fromisoformat(match.group(2)),
            )
        )

    if date_from is None and date_to is None:
        if not parsed_ranges:
            return True, "CSV из TG Wallet загружены.", files, []

        latest_covered_date = max(file_to for _, _, file_to in parsed_ranges)
        today = datetime.now(UTC).date()
        if latest_covered_date < today:
            missing_dates: list[date] = []
            cursor = latest_covered_date + timedelta(days=1)
            while cursor <= today:
                missing_dates.append(cursor)
                cursor += timedelta(days=1)
            preview = ", ".join(item.isoformat() for item in missing_dates[:5])
            suffix = " ..." if len(missing_dates) > 5 else ""
            return (
                False,
                f"Последний CSV из TG Wallet покрывает период только до {latest_covered_date.isoformat()}. Нужна новая выгрузка за даты: {preview}{suffix}",
                files,
                missing_dates,
            )
        return True, "CSV из TG Wallet актуальны на сегодня.", files, []

    if date_from is None or date_to is None:
        return True, "CSV из TG Wallet загружены.", files, []

    covered_dates: set[date] = set()
    matched_files: list[str] = []
    for filename, file_from, file_to in parsed_ranges:
        if file_to < date_from or file_from > date_to:
            continue
        matched_files.append(filename)
        cursor = max(file_from, date_from)
        last = min(file_to, date_to)
        while cursor <= last:
            covered_dates.add(cursor)
            cursor += timedelta(days=1)

    expected_dates: list[date] = []
    cursor = date_from
    while cursor <= date_to:
        expected_dates.append(cursor)
        cursor += timedelta(days=1)

    missing_dates = [item for item in expected_dates if item not in covered_dates]
    if missing_dates:
        preview = ", ".join(item.isoformat() for item in missing_dates[:5])
        suffix = " ..." if len(missing_dates) > 5 else ""
        return (
            False,
            f"За выбранный период не хватает CSV из TG Wallet. Нужна выгрузка за даты: {preview}{suffix}",
            matched_files,
            missing_dates,
        )

    return True, "CSV из TG Wallet покрывают выбранный период.", matched_files, []


def _empty_wallet_rate_stats() -> WalletRateStats:
    return WalletRateStats(
        completed_deals_with_rates=0,
        crypto_currency=None,
        total_fiat_amount=Decimal("0"),
        gross_crypto_amount=Decimal("0"),
        total_net_crypto_amount=Decimal("0"),
        total_fee_crypto_amount=Decimal("0"),
        weighted_listed_rate=None,
        weighted_net_rate=None,
    )


def _build_wallet_profit_stats(
    buy_stats: WalletRateStats,
    sell_stats: WalletRateStats,
) -> WalletProfitStats:
    crypto_currency = sell_stats.crypto_currency or buy_stats.crypto_currency
    matched_crypto_amount = min(buy_stats.total_net_crypto_amount, sell_stats.total_net_crypto_amount)
    inventory_delta_crypto_amount = buy_stats.total_net_crypto_amount - sell_stats.total_net_crypto_amount
    avg_buy_net_rate = buy_stats.weighted_net_rate
    avg_sell_net_rate = sell_stats.weighted_net_rate

    spread_per_crypto = None
    if avg_buy_net_rate is not None and avg_sell_net_rate is not None:
        spread_per_crypto = avg_sell_net_rate - avg_buy_net_rate

    estimated_profit_rub = Decimal("0")
    if spread_per_crypto is not None and matched_crypto_amount > 0:
        estimated_profit_rub = spread_per_crypto * matched_crypto_amount

    cash_flow_delta_rub = sell_stats.total_fiat_amount - buy_stats.total_fiat_amount

    return WalletProfitStats(
        crypto_currency=crypto_currency,
        buy_fiat_amount=buy_stats.total_fiat_amount,
        sell_fiat_amount=sell_stats.total_fiat_amount,
        buy_net_crypto_amount=buy_stats.total_net_crypto_amount,
        sell_net_crypto_amount=sell_stats.total_net_crypto_amount,
        matched_crypto_amount=matched_crypto_amount,
        inventory_delta_crypto_amount=inventory_delta_crypto_amount,
        avg_buy_net_rate=avg_buy_net_rate,
        avg_sell_net_rate=avg_sell_net_rate,
        spread_per_crypto=spread_per_crypto,
        estimated_profit_rub=estimated_profit_rub,
        cash_flow_delta_rub=cash_flow_delta_rub,
    )


def _reference_dt_for_reporting(deal: Deal) -> datetime:
    return deal.completed_at or deal.opened_at or deal.created_at


def _is_in_period(moment: datetime, date_from: date | None, date_to: date | None) -> bool:
    day = moment.date()
    if date_from is not None and day < date_from:
        return False
    if date_to is not None and day > date_to:
        return False
    return True


def _empty_wallet_trading_stats() -> WalletTradingStats:
    return WalletTradingStats(
        crypto_currency=None,
        matched_buy_deals=0,
        matched_sell_deals=0,
        buy_turnover_rub=Decimal("0"),
        sell_turnover_rub=Decimal("0"),
        total_turnover_rub=Decimal("0"),
        avg_buy_net_rate=None,
        avg_sell_net_rate=None,
        realized_profit_rub=Decimal("0"),
        realized_sell_crypto_amount=Decimal("0"),
        balance_crypto_amount=Decimal("0"),
    )


def _build_wallet_trading_stats(
    deals: list[Deal],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> WalletTradingStats:
    inventory: list[dict[str, Decimal]] = []
    crypto_currency: str | None = None
    matched_buy_deals = 0
    matched_sell_deals = 0
    buy_turnover_rub = Decimal("0")
    sell_turnover_rub = Decimal("0")
    buy_listed_total = Decimal("0")
    sell_listed_total = Decimal("0")
    buy_fiat_total = Decimal("0")
    sell_fiat_total = Decimal("0")
    realized_profit_rub = Decimal("0")
    realized_sell_crypto_amount = Decimal("0")
    oversold_crypto_amount = Decimal("0")

    ordered_deals = sorted(deals, key=_reference_dt_for_reporting)
    for deal in ordered_deals:
        raw_payload = deal.raw_payload or {}
        side = _wallet_side(raw_payload)
        fiat_amount = _to_decimal(raw_payload.get("fiat_amount")) or deal.expected_fiat_amount
        net_crypto_amount = _to_decimal(raw_payload.get("net_crypto_amount"))
        fee_crypto_amount = _to_decimal(raw_payload.get("paid_fee_crypto_amount")) or Decimal("0")
        effective_crypto_amount = _fee_adjusted_crypto_amount(raw_payload, side)
        if side not in {"buy", "sell"} or net_crypto_amount is None or net_crypto_amount <= 0:
            continue
        if effective_crypto_amount is None or effective_crypto_amount <= 0:
            continue

        crypto_currency = crypto_currency or raw_payload.get("crypto_currency")
        moment = _reference_dt_for_reporting(deal)
        in_period = _is_in_period(moment, date_from, date_to)
        unit_rate = fiat_amount / effective_crypto_amount
        listed_trade_crypto_amount = _listed_trade_crypto_amount(raw_payload, effective_crypto_amount)

        if side == "buy":
            inventory.append({"qty": net_crypto_amount, "unit_cost": unit_rate})
            if in_period:
                matched_buy_deals += 1
                buy_turnover_rub += fiat_amount
                buy_listed_total += listed_trade_crypto_amount
                buy_fiat_total += fiat_amount
            continue

        sell_qty_remaining = effective_crypto_amount
        matched_qty = Decimal("0")
        matched_cost_basis = Decimal("0")
        while sell_qty_remaining > 0 and inventory:
            lot = inventory[0]
            lot_qty = lot["qty"]
            consumed_qty = min(sell_qty_remaining, lot_qty)
            matched_qty += consumed_qty
            matched_cost_basis += consumed_qty * lot["unit_cost"]
            lot["qty"] = lot_qty - consumed_qty
            sell_qty_remaining -= consumed_qty
            if lot["qty"] <= 0:
                inventory.pop(0)

        if sell_qty_remaining > 0:
            oversold_crypto_amount += sell_qty_remaining

        if in_period:
            matched_sell_deals += 1
            sell_turnover_rub += fiat_amount
            sell_listed_total += listed_trade_crypto_amount
            sell_fiat_total += fiat_amount
            if matched_qty > 0:
                realized_sell_crypto_amount += matched_qty
                realized_profit_rub += (matched_qty * unit_rate) - matched_cost_basis

    avg_buy_net_rate = (buy_fiat_total / buy_listed_total) if buy_listed_total > 0 else None
    avg_sell_net_rate = (sell_fiat_total / sell_listed_total) if sell_listed_total > 0 else None
    open_inventory_crypto_amount = sum((lot["qty"] for lot in inventory), start=Decimal("0"))
    balance_crypto_amount = open_inventory_crypto_amount - oversold_crypto_amount

    return WalletTradingStats(
        crypto_currency=crypto_currency,
        matched_buy_deals=matched_buy_deals,
        matched_sell_deals=matched_sell_deals,
        buy_turnover_rub=buy_turnover_rub,
        sell_turnover_rub=sell_turnover_rub,
        total_turnover_rub=buy_turnover_rub + sell_turnover_rub,
        avg_buy_net_rate=avg_buy_net_rate,
        avg_sell_net_rate=avg_sell_net_rate,
        realized_profit_rub=realized_profit_rub,
        realized_sell_crypto_amount=realized_sell_crypto_amount,
        balance_crypto_amount=balance_crypto_amount,
    )


def _aggregate_wallet_rate_stats(deals: list[Deal], *, side: str | None = None) -> WalletRateStats:
    total_fiat = Decimal("0")
    total_net_crypto = Decimal("0")
    total_fee_crypto = Decimal("0")
    total_gross_crypto = Decimal("0")
    crypto_currency: str | None = None
    completed_deals_with_rates = 0

    for deal in deals:
        raw_payload = deal.raw_payload or {}
        deal_side = _wallet_side(raw_payload)
        if side is not None and deal_side != side:
            continue

        fiat_amount = _to_decimal(raw_payload.get("fiat_amount")) or deal.expected_fiat_amount
        net_crypto_amount = _to_decimal(raw_payload.get("net_crypto_amount"))
        fee_crypto_amount = _to_decimal(raw_payload.get("paid_fee_crypto_amount")) or Decimal("0")
        if net_crypto_amount is None or net_crypto_amount <= 0:
            continue

        total_fiat += fiat_amount
        total_net_crypto += net_crypto_amount
        total_fee_crypto += fee_crypto_amount
        total_gross_crypto += net_crypto_amount + fee_crypto_amount
        crypto_currency = crypto_currency or raw_payload.get("crypto_currency")
        completed_deals_with_rates += 1

    weighted_listed_rate = None
    weighted_net_rate = None
    if total_gross_crypto > 0:
        weighted_listed_rate = total_fiat / total_gross_crypto
    effective_crypto_total = total_net_crypto
    if effective_crypto_total > 0:
        weighted_net_rate = total_fiat / effective_crypto_total

    return WalletRateStats(
        completed_deals_with_rates=completed_deals_with_rates,
        crypto_currency=crypto_currency,
        total_fiat_amount=total_fiat,
        gross_crypto_amount=total_gross_crypto,
        total_net_crypto_amount=total_net_crypto,
        total_fee_crypto_amount=total_fee_crypto,
        weighted_listed_rate=weighted_listed_rate,
        weighted_net_rate=weighted_net_rate,
    )


def build_dashboard_snapshot(
    session: Session,
    recent_deals_limit: int = 10,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> TelegramDashboardSnapshot:
    summary = build_reconciliation_summary(session, date_from=date_from, date_to=date_to)
    deal_filters = _deal_period_filters(date_from, date_to)
    bank_filters = _bank_period_filters(date_from, date_to)
    receipt_filters = _receipt_period_filters(date_from, date_to)

    status_rows = session.execute(
        select(Deal.status, func.count()).where(*deal_filters).group_by(Deal.status)
    ).all()
    status_counts = {str(status.value if hasattr(status, "value") else status): count for status, count in status_rows}

    total_bank_transactions = session.scalar(select(func.count()).select_from(BankTransaction).where(*bank_filters)) or 0
    total_receipts = session.scalar(select(func.count()).select_from(FiscalReceipt).where(*receipt_filters)) or 0
    sale_receipts = session.scalar(
        select(func.count()).select_from(FiscalReceipt).where(FiscalReceipt.receipt_type == ReceiptType.sale, *receipt_filters)
    ) or 0
    refund_receipts = session.scalar(
        select(func.count()).select_from(FiscalReceipt).where(FiscalReceipt.receipt_type == ReceiptType.refund, *receipt_filters)
    ) or 0

    recent_deals = session.scalars(
        select(Deal).where(*deal_filters).order_by(func.coalesce(Deal.opened_at, Deal.created_at).desc()).limit(recent_deals_limit)
    ).all()
    completed_wallet_deals = session.scalars(
        select(Deal).where(
            Deal.source.in_(TRADED_DEAL_SOURCES),
            Deal.completed_at.is_not(None),
            Deal.status.in_([DealStatus.matched, DealStatus.receipted]),
            Deal.bank_transactions.any(),
            *deal_filters,
        )
    ).all()
    wallet_history_for_profit = session.scalars(
        select(Deal).where(
            Deal.source.in_(TRADED_DEAL_SOURCES),
            Deal.completed_at.is_not(None),
            Deal.status.in_([DealStatus.matched, DealStatus.receipted]),
            Deal.bank_transactions.any(),
            *([] if date_to is None else [func.date(func.coalesce(Deal.completed_at, Deal.opened_at, Deal.created_at)) <= date_to.isoformat()]),
        )
    ).all()
    unmatched_wallet_deals = session.scalars(
        select(Deal).where(
            Deal.source == "wallet",
            Deal.completed_at.is_not(None),
            ~Deal.bank_transactions.any(),
            *deal_filters,
        ).order_by(func.coalesce(Deal.completed_at, Deal.opened_at, Deal.created_at).desc())
    ).all()
    wallet_csv_has_coverage, wallet_csv_message, wallet_csv_files, wallet_csv_missing_dates = _wallet_csv_coverage_v2(
        session,
        date_from=date_from,
        date_to=date_to,
    )
    wallet_rate_stats = _aggregate_wallet_rate_stats(completed_wallet_deals)
    wallet_buy_rate_stats = _aggregate_wallet_rate_stats(completed_wallet_deals, side="buy")
    wallet_sell_rate_stats = _aggregate_wallet_rate_stats(completed_wallet_deals, side="sell")
    wallet_profit_stats = _build_wallet_profit_stats(wallet_buy_rate_stats, wallet_sell_rate_stats)
    wallet_trading_stats = _build_wallet_trading_stats(
        wallet_history_for_profit,
        date_from=date_from,
        date_to=date_to,
    )

    return TelegramDashboardSnapshot(
        summary=summary,
        wallet_rate_stats=wallet_rate_stats,
        wallet_buy_rate_stats=wallet_buy_rate_stats,
        wallet_sell_rate_stats=wallet_sell_rate_stats,
        wallet_profit_stats=wallet_profit_stats,
        wallet_trading_stats=wallet_trading_stats,
        status_counts=status_counts,
        total_bank_transactions=total_bank_transactions,
        total_receipts=total_receipts,
        sale_receipts=sale_receipts,
        refund_receipts=refund_receipts,
        date_from=date_from,
        date_to=date_to,
        wallet_csv_has_coverage=wallet_csv_has_coverage,
        wallet_csv_message=wallet_csv_message,
        wallet_csv_files=wallet_csv_files,
        wallet_csv_missing_dates=wallet_csv_missing_dates,
        unmatched_wallet_deals_count=len(unmatched_wallet_deals),
        unmatched_wallet_deal_ids=[deal.external_id for deal in unmatched_wallet_deals[:10]],
        recent_deals=[DealOut.model_validate(deal) for deal in recent_deals],
    )
