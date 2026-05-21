from __future__ import annotations

import argparse
import json
from datetime import date

from app.sync.client import CoreApiClient
from app.sync.config import sync_settings
from app.sync.daemon import AutoSyncDaemon


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automatic synchronization for Wallet, Sber, and OFD")
    subparsers = parser.add_subparsers(dest="command", required=True)

    daemon_parser = subparsers.add_parser("daemon", help="Run background auto-sync loop")
    daemon_parser.add_argument("--api-base-url", default=sync_settings.api_base_url, help="Core API base URL")
    daemon_parser.add_argument(
        "--poll-interval",
        type=int,
        default=sync_settings.poll_interval_seconds,
        help="Polling interval in seconds",
    )

    backfill_parser = subparsers.add_parser(
        "backfill-sber",
        help="Pull Sber statements for a date range and immediately run reconciliation",
    )
    backfill_parser.add_argument("--api-base-url", default=sync_settings.api_base_url, help="Core API base URL")
    backfill_parser.add_argument("--account-number", default="", help="Account number to pull. Optional if configured on the server.")
    backfill_parser.add_argument("--date-from", required=True, help="Start date in YYYY-MM-DD")
    backfill_parser.add_argument("--date-to", required=True, help="End date in YYYY-MM-DD")
    backfill_parser.add_argument("--page", type=int, default=1, help="Start page number")
    backfill_parser.add_argument(
        "--first-page-only",
        action="store_true",
        help="Only pull the first page for each day instead of iterating all pages",
    )
    backfill_parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Only import Sber data without running matching and receipt reconciliation",
    )
    return parser


def run_backfill_sber(
    *,
    api_base_url: str,
    account_number: str,
    date_from: str,
    date_to: str,
    page: int,
    first_page_only: bool,
    skip_reconcile: bool,
) -> int:
    client = CoreApiClient(base_url=api_base_url)
    result = client.pull_sber_statements(
        account_number=account_number or None,
        date_from=date.fromisoformat(date_from),
        date_to=date.fromisoformat(date_to),
        page=page,
        all_pages=not first_page_only,
    )

    output: dict[str, object] = {
        "sber_pull_status_code": result.status_code,
        "sber_pull_response": json.loads(result.response_body),
    }

    if not skip_reconcile:
        matching = client.run_matching()
        receipts = client.run_receipt_reconciliation()
        classification = client.run_classification()
        report = client.get_report()
        output.update(
            {
                "matching_status_code": matching.status_code,
                "receipt_reconciliation_status_code": receipts.status_code,
                "classification_status_code": classification.status_code,
                "report": report.model_dump(mode="json"),
            }
        )

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "daemon":
        daemon = AutoSyncDaemon(api_base_url=args.api_base_url, poll_interval_seconds=args.poll_interval)
        daemon.run_forever()
        return 0
    if args.command == "backfill-sber":
        return run_backfill_sber(
            api_base_url=args.api_base_url,
            account_number=args.account_number,
            date_from=args.date_from,
            date_to=args.date_to,
            page=args.page,
            first_page_only=args.first_page_only,
            skip_reconcile=args.skip_reconcile,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
