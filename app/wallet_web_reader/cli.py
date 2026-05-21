from __future__ import annotations

import argparse
import json

from app.sync.client import CoreApiClient
from app.wallet_web_reader.config import wallet_web_reader_settings
from app.wallet_web_reader.playwright_reader import download_wallet_csv, wait_for_manual_login
from app.reader.sync import import_wallet_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wallet Web read-only export automation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manual_login_parser = subparsers.add_parser(
        "manual-login",
        help="Open Wallet in a browser and save storage state after manual login",
    )
    manual_login_parser.add_argument(
        "--storage-state",
        default=wallet_web_reader_settings.storage_state_path,
        help="Path to save authenticated browser state",
    )
    manual_login_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="How long to wait for manual login completion",
    )

    download_parser = subparsers.add_parser(
        "download-csv",
        help="Use saved Wallet session to download the latest P2P CSV export",
    )
    download_parser.add_argument(
        "--storage-state",
        default=wallet_web_reader_settings.storage_state_path,
        help="Path to authenticated browser state",
    )
    download_parser.add_argument(
        "--downloads-dir",
        default=wallet_web_reader_settings.downloads_dir,
        help="Directory where the CSV should be saved",
    )

    import_parser = subparsers.add_parser(
        "import-csv",
        help="Download the latest Wallet CSV, import it into the core API, and run reconciliation",
    )
    import_parser.add_argument(
        "--storage-state",
        default=wallet_web_reader_settings.storage_state_path,
        help="Path to authenticated browser state",
    )
    import_parser.add_argument(
        "--downloads-dir",
        default=wallet_web_reader_settings.downloads_dir,
        help="Directory where the CSV should be saved",
    )
    import_parser.add_argument(
        "--api-base-url",
        default=wallet_web_reader_settings.api_base_url,
        help="Core API base URL",
    )
    import_parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Only import the CSV without running matching and receipt reconciliation",
    )

    return parser


def run_manual_login(storage_state: str, timeout_seconds: int) -> int:
    saved = wait_for_manual_login(storage_state, timeout_ms=timeout_seconds * 1000)
    print(str(saved))
    return 0


def run_download_csv(storage_state: str, downloads_dir: str) -> int:
    saved = download_wallet_csv(storage_state, downloads_dir=downloads_dir)
    print(str(saved))
    return 0


def run_import_csv(storage_state: str, downloads_dir: str, api_base_url: str, skip_reconcile: bool) -> int:
    csv_path = download_wallet_csv(storage_state, downloads_dir=downloads_dir)
    import_result = import_wallet_csv(str(csv_path), api_base_url=api_base_url)

    result: dict[str, object] = {
        "csv_path": str(csv_path),
        "wallet_imported_count": import_result.imported_count,
        "wallet_status_code": import_result.status_code,
    }

    if not skip_reconcile:
        client = CoreApiClient(base_url=api_base_url)
        matching = client.run_matching()
        receipts = client.run_receipt_reconciliation()
        classification = client.run_classification()
        report = client.get_report()
        result.update(
            {
                "matching_status_code": matching.status_code,
                "receipt_reconciliation_status_code": receipts.status_code,
                "classification_status_code": classification.status_code,
                "report": report.model_dump(mode="json"),
            }
        )

    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "manual-login":
        return run_manual_login(args.storage_state, args.timeout_seconds)
    if args.command == "download-csv":
        return run_download_csv(args.storage_state, args.downloads_dir)
    if args.command == "import-csv":
        return run_import_csv(args.storage_state, args.downloads_dir, args.api_base_url, args.skip_reconcile)

    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
