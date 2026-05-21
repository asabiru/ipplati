from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.reader.adapters.csv_adapter import load_wallet_csv
from app.reader.sync import import_wallet_csv
from app.sync.client import CoreApiClient
from app.telegram_bot.client import TelegramBotApi
from app.telegram_bot.config import telegram_bot_settings


CSV_FILENAME_RE = re.compile(r"^p2p-order-history_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.csv$", re.IGNORECASE)


@dataclass(slots=True)
class WalletCsvUploadResult:
    saved_path: Path
    processed_rows: int
    wallet_status_code: int
    matching_status_code: int
    receipt_reconciliation_status_code: int
    classification_status_code: int
    report: dict


def is_wallet_csv_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return bool(CSV_FILENAME_RE.match(Path(filename).name))


def _uploads_dir() -> Path:
    path = Path(telegram_bot_settings.uploads_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_telegram_wallet_csv(telegram: TelegramBotApi, *, file_id: str, filename: str) -> Path:
    file_info = telegram.get_file(file_id)
    file_path = file_info["file_path"]
    target = _uploads_dir() / Path(filename).name
    return telegram.download_file(file_path, target)


def import_wallet_csv_from_upload(saved_path: str | Path) -> WalletCsvUploadResult:
    path = Path(saved_path)
    processed_rows = len(load_wallet_csv(path))
    import_result = import_wallet_csv(str(path), api_base_url=telegram_bot_settings.api_base_url)

    client = CoreApiClient(base_url=telegram_bot_settings.api_base_url)
    matching = client.run_matching()
    receipts = client.run_receipt_reconciliation()
    classification = client.run_classification()
    report = client.get_report()

    return WalletCsvUploadResult(
        saved_path=path,
        processed_rows=processed_rows,
        wallet_status_code=import_result.status_code,
        matching_status_code=matching.status_code,
        receipt_reconciliation_status_code=receipts.status_code,
        classification_status_code=classification.status_code,
        report=report.model_dump(mode="json"),
    )
