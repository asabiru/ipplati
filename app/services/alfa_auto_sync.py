from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import threading

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.services.classification import classify_all_deals, link_bank_return_transactions
from app.services.matching import deduplicate_linked_transactions, run_matching
from app.services.receipt_matching import reconcile_receipts
from app.services.alfa_auth import import_statement_transactions_range


_ALFA_SYNC_LOCK = threading.Lock()
_ALFA_SYNC_STATE_LOCK = threading.Lock()
_LAST_ALFA_SYNC_AT: datetime | None = None


@dataclass(slots=True)
class AlfaAutoSyncResult:
    executed: bool
    reason: str
    imported_transactions: int = 0
    total_transactions: int = 0
    pages_processed: int = 0
    matched_deals: int = 0
    linked_receipts: int = 0
    classified_deals: int = 0
    linked_bank_returns: int = 0
    deduplicated_links: int = 0
    date_from: date | None = None
    date_to: date | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _alfa_lookback_range() -> tuple[date, date]:
    today = _utc_now().date()
    lookback_days = max(1, settings.alfa_auto_sync_lookback_days)
    return today - timedelta(days=lookback_days - 1), today


def _remember_alfa_sync_run(moment: datetime) -> None:
    global _LAST_ALFA_SYNC_AT
    with _ALFA_SYNC_STATE_LOCK:
        _LAST_ALFA_SYNC_AT = moment


def _last_alfa_sync_at() -> datetime | None:
    with _ALFA_SYNC_STATE_LOCK:
        return _LAST_ALFA_SYNC_AT


def should_run_alfa_sync(*, force: bool = False) -> bool:
    if not settings.alfa_auto_sync_enabled:
        return False
    if not settings.alfa_default_account_number:
        return False
    if not settings.alfa_api_key and not settings.alfa_client_id:
        return False
    if force:
        return True
    last_run = _last_alfa_sync_at()
    if last_run is None:
        return True
    min_interval = timedelta(seconds=max(0, settings.alfa_auto_sync_min_interval_seconds))
    return _utc_now() - last_run >= min_interval


def auto_sync_recent_alfa(session: Session, *, force: bool = False, reason: str = "manual") -> AlfaAutoSyncResult:
    if not should_run_alfa_sync(force=force):
        date_from, date_to = _alfa_lookback_range()
        return AlfaAutoSyncResult(executed=False, reason="throttled", date_from=date_from, date_to=date_to)

    if not _ALFA_SYNC_LOCK.acquire(blocking=False):
        date_from, date_to = _alfa_lookback_range()
        return AlfaAutoSyncResult(executed=False, reason="busy", date_from=date_from, date_to=date_to)

    try:
        date_from, date_to = _alfa_lookback_range()
        imported, total_transactions, pages_processed, _raw = import_statement_transactions_range(
            session,
            account_number=settings.alfa_default_account_number,
            date_from=date_from,
            date_to=date_to,
            all_pages=True,
            start_page=1,
        )
        deduplicated_links = deduplicate_linked_transactions(session)
        matched = len(run_matching(session))
        linked_receipts = reconcile_receipts(session)
        linked_bank_returns = link_bank_return_transactions(session)
        classified = classify_all_deals(session)
        _remember_alfa_sync_run(_utc_now())
        session.expire_all()
        return AlfaAutoSyncResult(
            executed=True,
            reason=reason,
            imported_transactions=imported,
            total_transactions=total_transactions,
            pages_processed=pages_processed,
            matched_deals=matched,
            linked_receipts=linked_receipts,
            classified_deals=classified,
            linked_bank_returns=linked_bank_returns,
            deduplicated_links=deduplicated_links,
            date_from=date_from,
            date_to=date_to,
        )
    finally:
        _ALFA_SYNC_LOCK.release()


def auto_sync_recent_alfa_safe(*, force: bool = False, reason: str = "manual") -> AlfaAutoSyncResult:
    session = SessionLocal()
    try:
        return auto_sync_recent_alfa(session, force=force, reason=reason)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alfa_auto_sync_loop(stop_event: threading.Event) -> None:
    poll_interval = max(30, settings.alfa_auto_sync_poll_interval_seconds)
    while not stop_event.is_set():
        try:
            auto_sync_recent_alfa_safe(reason="background")
        except Exception as exc:
            print(f"[alfa-auto-sync] error: {exc}")
        stop_event.wait(poll_interval)
