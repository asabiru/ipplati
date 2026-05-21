from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def _normalize_side(ad_type: str, role: str = "") -> str | None:
    normalized_ad_type = ad_type.strip().lower()
    normalized_role = role.strip().lower()
    combined = f"{normalized_ad_type} {normalized_role}"
    if any(token in combined for token in ("buy", "purchase", "buyer", "покуп")):
        return "buy"
    if any(token in combined for token in ("sell", "sale", "seller", "прод")):
        return "sell"
    return None


def _normalize_status(row: dict[str, str]) -> str:
    explicit_status = (row.get("Order Status") or row.get("Status") or "").strip().lower()
    if explicit_status in {"refund", "refunded", "returned", "reversed", "chargeback"}:
        return "returned"
    if explicit_status in {"disputed", "third_party_review"}:
        return "disputed"
    if explicit_status in {"cancelled", "canceled"}:
        return "canceled"
    completion_time = (row.get("Completion Time") or "").strip()
    return "completed" if completion_time else "awaiting_payment"


def load_wallet_csv(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        deals: list[dict[str, Any]] = []
        for row in reader:
            order_number = (row.get("Order Number") or "").strip()
            if not order_number:
                continue

            ad_type = (row.get("Ad Type") or "").strip()
            role = (row.get("Role") or "").strip()
            fiat_amount = (row.get("Fiat Amount") or "").strip()
            fiat_currency = (row.get("Fiat Currency") or "").strip() or "RUB"
            payment_method = (row.get("Payment Method") or "").strip() or None
            creation_time = (row.get("Creation Time") or "").strip() or None
            completion_time = (row.get("Completion Time") or "").strip() or None
            crypto_currency = (row.get("Crypto Currency") or "").strip() or None

            deals.append(
                {
                    "external_id": order_number,
                    "expected_fiat_amount": fiat_amount,
                    "fiat_currency": fiat_currency,
                    "counterparty_name": None,
                    "counterparty_wallet_id": None,
                    "payment_method": payment_method,
                    "opened_at": creation_time,
                    "completed_at": completion_time,
                    "status": _normalize_status(row),
                    "notes": f"Imported from Wallet CSV: {ad_type} / {role}",
                    "raw_payload": {
                        "source_file": str(input_path),
                        "ad_number": (row.get("Ad Number") or "").strip(),
                        "ad_type": ad_type,
                        "side": _normalize_side(ad_type, role),
                        "role": role,
                        "net_crypto_amount": (row.get("Net Crypto Amount") or "").strip(),
                        "paid_fee_crypto_amount": (row.get("Paid Fee Crypto Amount") or "").strip(),
                        "crypto_currency": crypto_currency,
                        "fiat_amount": fiat_amount,
                        "fiat_currency": fiat_currency,
                        "price": (row.get("Price") or "").strip(),
                        "payment_method": payment_method,
                        "creation_time": creation_time,
                        "completion_time": completion_time,
                    },
                }
            )

    return deals
