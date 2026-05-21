from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable

from app.schemas import ReceiptBatchIn


def _pick(row: dict[str, Any], aliases: Iterable[str]) -> str | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _clean_amount(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    filtered = "".join(ch for ch in value if ch.isdigit() or ch == ".")
    return filtered or None


def _parse_issued_at(raw: str | None) -> str | None:
    if not raw:
        return None

    candidates = [
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d.%m.%Y",
    ]
    text = raw.strip()
    for fmt in candidates:
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return text


def _parse_receipt_type(row: dict[str, Any]) -> str:
    raw = (
        _pick(
            row,
            [
                "receipt type",
                "type",
                "тип чека",
                "признак расчета",
                "операция",
            ],
        )
        or ""
    ).lower()
    if "возврат" in raw or "refund" in raw:
        return "refund"
    return "sale"


def _build_external_id(row: dict[str, Any]) -> str | None:
    direct = _pick(
        row,
        [
            "receipt id",
            "check id",
            "document number",
            "номер чека",
            "фд",
            "фн",
            "uuid",
            "id",
        ],
    )
    if direct:
        return direct

    fn = _pick(row, ["фн", "fn"])
    fd = _pick(row, ["фд", "fd"])
    fp = _pick(row, ["фп", "fp"])
    if fn and fd:
        suffix = f"-{fp}" if fp else ""
        return f"{fn}-{fd}{suffix}"
    return None


def normalize_ofd_rows(rows: list[dict[str, Any]]) -> ReceiptBatchIn:
    receipts: list[dict[str, Any]] = []

    for row in rows:
        external_id = _build_external_id(row)
        amount = _clean_amount(_pick(row, ["amount", "total", "сумма", "итог"]))
        issued_at = _parse_issued_at(_pick(row, ["date", "issued at", "дата", "дата чека", "время", "дата и время"]))

        if not external_id or not amount or not issued_at:
            continue

        raw_payload = {"source": "ofd_playwright", "scraped_row": row}
        related_deal_id = _pick(row, ["related deal id", "wallet order", "order number", "сделка", "заказ"])
        if related_deal_id:
            raw_payload["related_deal_external_id"] = related_deal_id

        receipts.append(
            {
                "external_id": external_id,
                "deal_external_id": related_deal_id,
                "receipt_type": _parse_receipt_type(row),
                "status": "issued",
                "amount": str(Decimal(amount)),
                "issued_at": issued_at,
                "raw_payload": raw_payload,
            }
        )

    return ReceiptBatchIn(receipts=receipts)


def _kopecks_to_rub(value: int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    return (Decimal(str(value)) / Decimal("100")).copy_abs()


def _document_receipt_type(document: dict[str, Any]) -> str:
    operation_type = int(document.get("operationType") or 0)
    total_sum = Decimal(str(document.get("sum") or 0))
    if operation_type == 2 or total_sum < 0:
        return "refund"
    return "sale"


def _is_fiscal_receipt_document(document: dict[str, Any]) -> bool:
    # Astral also returns shift open/close reports and other service documents.
    # For reconciliation we only keep fiscal receipts.
    return str(document.get("documentType")) == "3"


def normalize_ofd_api_documents(documents: list[dict[str, Any]]) -> ReceiptBatchIn:
    receipts: list[dict[str, Any]] = []

    for document in documents:
        if not _is_fiscal_receipt_document(document):
            continue

        issue_date = document.get("issueDate")
        amount = _kopecks_to_rub(document.get("sum"))
        fd = document.get("checkNumber")
        fn = document.get("fiscalDriveNumber")
        fiscal_sign = document.get("fiscalSign")

        if issue_date is None or amount is None or fd is None or fn is None:
            continue

        issued_at = datetime.fromtimestamp(int(issue_date), tz=UTC).isoformat()
        external_id = f"{fn}-{fd}-{fiscal_sign or issue_date}"
        raw_payload = {"source": "ofd_api", "document": document}

        receipts.append(
            {
                "external_id": external_id,
                "deal_external_id": None,
                "receipt_type": _document_receipt_type(document),
                "status": "issued",
                "amount": str(amount),
                "issued_at": issued_at,
                "raw_payload": raw_payload,
            }
        )

    return ReceiptBatchIn(receipts=receipts)
