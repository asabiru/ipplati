from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from app.reader.adapters.csv_adapter import load_wallet_csv
from app.reader.client import ReaderImportResult, WalletApiClient
from app.reader.config import reader_settings
from app.reader.normalizer import normalize_wallet_batch


@dataclass(slots=True)
class CsvSyncState:
    source_path: str
    imported_count: int
    changed_at: float


def import_wallet_csv(path: str, api_base_url: str | None = None) -> ReaderImportResult:
    raw_deals = load_wallet_csv(path)
    payload = normalize_wallet_batch(raw_deals)
    client = WalletApiClient(base_url=api_base_url)
    return client.import_deals(payload)


def watch_wallet_csv(
    path: str,
    api_base_url: str | None = None,
    poll_interval_seconds: int | None = None,
) -> None:
    poll_interval = poll_interval_seconds or reader_settings.poll_interval_seconds
    source_path = Path(path)
    last_mtime: float | None = None

    while True:
        try:
            if source_path.exists():
                mtime = source_path.stat().st_mtime
                if last_mtime is None or mtime > last_mtime:
                    result = import_wallet_csv(str(source_path), api_base_url=api_base_url)
                    state = CsvSyncState(
                        source_path=str(source_path),
                        imported_count=result.imported_count,
                        changed_at=mtime,
                    )
                    print(
                        f"[wallet-sync] imported={state.imported_count} "
                        f"path={state.source_path} changed_at={state.changed_at:.0f}"
                    )
                    last_mtime = mtime
            time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("[wallet-sync] stopped")
            break
        except Exception as exc:
            print(f"[wallet-sync] error: {exc}")
            time.sleep(poll_interval)


def find_latest_wallet_csv(directory: str, pattern: str = "p2p-order-history_*.csv") -> Path | None:
    candidates = sorted(Path(directory).glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def watch_wallet_csv_directory(
    directory: str,
    api_base_url: str | None = None,
    pattern: str = "p2p-order-history_*.csv",
    poll_interval_seconds: int | None = None,
) -> None:
    poll_interval = poll_interval_seconds or reader_settings.poll_interval_seconds
    last_signature: tuple[str, float] | None = None

    while True:
        try:
            latest = find_latest_wallet_csv(directory, pattern=pattern)
            if latest is not None:
                signature = (str(latest), latest.stat().st_mtime)
                if last_signature is None or signature != last_signature:
                    result = import_wallet_csv(str(latest), api_base_url=api_base_url)
                    print(
                        f"[wallet-sync] imported={result.imported_count} "
                        f"path={latest} changed_at={signature[1]:.0f}"
                    )
                    last_signature = signature
            time.sleep(poll_interval)
        except KeyboardInterrupt:
            print("[wallet-sync] stopped")
            break
        except Exception as exc:
            print(f"[wallet-sync] error: {exc}")
            time.sleep(poll_interval)
