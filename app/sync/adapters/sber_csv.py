from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from app.models import TransactionDirection
from app.schemas import BankTransactionBatchIn


def _pick(row: dict[str, str], aliases: Iterable[str]) -> str | None:
    for alias in aliases:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _parse_direction(row: dict[str, str]) -> TransactionDirection:
    raw = (_pick(row, ["Direction", "Тип операции", "Operation Type", "Debit/Credit"]) or "").lower()
    if any(token in raw for token in ["out", "debit", "исход", "спис", "withdraw", "refund"]):
        return TransactionDirection.outgoing
    return TransactionDirection.incoming


def load_sber_csv(path: str | Path) -> BankTransactionBatchIn:
    input_path = Path(path)
    transactions: list[dict] = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            external_id = _pick(row, ["Operation Id", "Document Number", "ID", "Transaction ID", "Номер документа"])
            amount = _pick(row, ["Amount", "Сумма", "Transaction Amount"])
            booked_at = _pick(row, ["Date", "Operation Date", "Дата", "Booking Date"])
            if not external_id or not amount or not booked_at:
                continue

            description = _pick(row, ["Description", "Назначение платежа", "Comment", "Details"])
            related_deal_id = _pick(row, ["Related Deal ID", "Wallet Order", "Order Number", "Связанная сделка"])
            return_reason = _pick(row, ["Return Reason", "Причина возврата"])

            raw_payload = {
                "source_file": str(input_path),
                "description": description,
            }
            if related_deal_id:
                raw_payload["related_deal_external_id"] = related_deal_id
            if return_reason:
                raw_payload["return_reason"] = return_reason

            transactions.append(
                {
                    "external_id": external_id,
                    "amount": amount.replace(" ", ""),
                    "currency": _pick(row, ["Currency", "Валюта"]) or "RUB",
                    "direction": _parse_direction(row).value,
                    "payer_name": _pick(row, ["Payer", "Sender", "Плательщик", "Контрагент"]),
                    "payer_account_masked": _pick(row, ["Account", "Счет", "Payer Account"]),
                    "reference": description,
                    "booked_at": booked_at,
                    "raw_payload": raw_payload,
                }
            )

    return BankTransactionBatchIn(transactions=transactions)
