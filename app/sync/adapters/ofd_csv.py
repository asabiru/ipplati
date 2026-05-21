from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from app.schemas import ReceiptBatchIn


def _pick(row: dict[str, str], aliases: Iterable[str]) -> str | None:
    for alias in aliases:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _parse_receipt_type(row: dict[str, str]) -> str:
    raw = (_pick(row, ["Receipt Type", "Type", "Тип чека", "Признак расчета"]) or "").lower()
    if any(token in raw for token in ["refund", "возврат"]):
        return "refund"
    return "sale"


def load_ofd_csv(path: str | Path) -> ReceiptBatchIn:
    input_path = Path(path)
    receipts: list[dict] = []
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            external_id = _pick(row, ["Receipt ID", "Check ID", "ФНД", "Document Number", "Номер чека"])
            amount = _pick(row, ["Amount", "Total", "Сумма"])
            issued_at = _pick(row, ["Date", "Issued At", "Дата", "Время чека"])
            if not external_id or not amount or not issued_at:
                continue

            deal_external_id = _pick(row, ["Related Deal ID", "Wallet Order", "Order Number", "Связанная сделка"])
            receipt_type = _parse_receipt_type(row)
            raw_payload = {
                "source_file": str(input_path),
                "raw_type": _pick(row, ["Receipt Type", "Type", "Тип чека", "Признак расчета"]),
            }
            if deal_external_id:
                raw_payload["related_deal_external_id"] = deal_external_id

            receipts.append(
                {
                    "external_id": external_id,
                    "deal_external_id": deal_external_id,
                    "receipt_type": receipt_type,
                    "status": "issued",
                    "amount": amount.replace(" ", ""),
                    "issued_at": issued_at,
                    "raw_payload": raw_payload,
                }
            )

    return ReceiptBatchIn(receipts=receipts)
