from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from app.reader.adapters.csv_adapter import load_wallet_csv
from app.reader.normalizer import normalize_wallet_batch
from app.schemas import BankTransactionBatchIn, ReceiptBatchIn
from app.sync.adapters.ofd_csv import load_ofd_csv
from app.sync.adapters.sber_csv import load_sber_csv
from app.sync.client import CoreApiClient
from app.sync.config import sync_settings


@dataclass(slots=True)
class SourceState:
    name: str
    pattern: str
    directory: str
    last_signature: tuple[str, float] | None = None


class AutoSyncDaemon:
    def __init__(self, api_base_url: str | None = None, poll_interval_seconds: int | None = None) -> None:
        self.client = CoreApiClient(base_url=api_base_url)
        self.poll_interval_seconds = poll_interval_seconds or sync_settings.poll_interval_seconds
        self.wallet_state = SourceState("wallet", sync_settings.wallet_pattern, sync_settings.wallet_directory)
        self.sber_state = SourceState("sber", sync_settings.sber_pattern, sync_settings.sber_directory)
        self.ofd_state = SourceState("ofd", sync_settings.ofd_pattern, sync_settings.ofd_directory)

    def _find_latest(self, state: SourceState) -> tuple[Path, tuple[str, float]] | None:
        candidates = sorted(Path(state.directory).glob(state.pattern), key=lambda item: item.stat().st_mtime, reverse=True)
        if not candidates:
            return None
        latest = candidates[0]
        signature = (str(latest), latest.stat().st_mtime)
        return latest, signature

    def _sync_wallet(self, path: Path) -> None:
        payload = normalize_wallet_batch(load_wallet_csv(path))
        result = self.client.import_wallet(payload)
        print(f"[sync] wallet imported status={result.status_code} path={path}")

    def _sync_sber(self, path: Path) -> None:
        payload: BankTransactionBatchIn = load_sber_csv(path)
        result = self.client.import_bank_transactions(payload)
        print(f"[sync] sber imported status={result.status_code} path={path}")

    def _sync_ofd(self, path: Path) -> None:
        payload: ReceiptBatchIn = load_ofd_csv(path)
        result = self.client.import_receipts(payload)
        print(f"[sync] ofd imported status={result.status_code} path={path}")

    def _check_source(self, state: SourceState, sync_func) -> bool:
        latest = self._find_latest(state)
        if latest is None:
            return False
        path, signature = latest
        if state.last_signature == signature:
            return False
        sync_func(path)
        state.last_signature = signature
        return True

    def tick(self) -> None:
        changed = False
        changed |= self._check_source(self.wallet_state, self._sync_wallet)
        changed |= self._check_source(self.sber_state, self._sync_sber)
        changed |= self._check_source(self.ofd_state, self._sync_ofd)
        if changed:
            self.client.run_matching()
            self.client.run_receipt_reconciliation()
            self.client.run_classification()
            report = self.client.get_report()
            print(
                "[sync] reconciliation "
                f"deals={report.total_deals} matched={report.matched_deals} "
                f"receipted={report.receipted_deals} returns={report.return_detected_deals}"
            )

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
                time.sleep(self.poll_interval_seconds)
            except KeyboardInterrupt:
                print("[sync] stopped")
                break
            except Exception as exc:
                print(f"[sync] error: {exc}")
                time.sleep(self.poll_interval_seconds)
