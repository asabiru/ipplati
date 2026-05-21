from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.reader.adapters.csv_adapter import load_wallet_csv
from app.reader.adapters.file_adapter import load_wallet_export
from app.reader.client import WalletApiClient
from app.reader.config import reader_settings
from app.reader.normalizer import normalize_wallet_batch
from app.reader.sync import import_wallet_csv, watch_wallet_csv, watch_wallet_csv_directory
from app.sync.client import CoreApiClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wallet read-only reader tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-file", help="Import Wallet deals from a JSON file")
    import_parser.add_argument("--input", required=True, help="Path to JSON file with raw Wallet deals")
    import_parser.add_argument("--api-base-url", default=reader_settings.api_base_url, help="Core API base URL")
    import_parser.add_argument("--dry-run", action="store_true", help="Print normalized payload without sending it")

    normalize_parser = subparsers.add_parser("normalize-file", help="Normalize Wallet deals to API payload JSON")
    normalize_parser.add_argument("--input", required=True, help="Path to JSON file with raw Wallet deals")
    normalize_parser.add_argument("--output", required=True, help="Path to write normalized payload JSON")

    import_csv_parser = subparsers.add_parser("import-csv", help="Import Wallet CSV export into the core API")
    import_csv_parser.add_argument("--input", required=True, help="Path to Wallet CSV export")
    import_csv_parser.add_argument("--api-base-url", default=reader_settings.api_base_url, help="Core API base URL")
    import_csv_parser.add_argument("--dry-run", action="store_true", help="Print normalized payload without sending it")

    process_csv_parser = subparsers.add_parser(
        "process-csv",
        help="Import a manually exported Wallet CSV and immediately run reconciliation",
    )
    process_csv_parser.add_argument("--input", required=True, help="Path to Wallet CSV export")
    process_csv_parser.add_argument("--api-base-url", default=reader_settings.api_base_url, help="Core API base URL")
    process_csv_parser.add_argument(
        "--skip-reconcile",
        action="store_true",
        help="Only import the CSV without running matching and receipt reconciliation",
    )

    watch_csv_parser = subparsers.add_parser("watch-csv", help="Watch one Wallet CSV file and reimport on changes")
    watch_csv_parser.add_argument("--input", required=True, help="Path to Wallet CSV export")
    watch_csv_parser.add_argument("--api-base-url", default=reader_settings.api_base_url, help="Core API base URL")
    watch_csv_parser.add_argument(
        "--poll-interval",
        type=int,
        default=reader_settings.poll_interval_seconds,
        help="How often to check for file changes in seconds",
    )

    watch_dir_parser = subparsers.add_parser(
        "watch-downloads",
        help="Watch a directory for the latest Wallet CSV export and reimport it automatically",
    )
    watch_dir_parser.add_argument("--directory", required=True, help="Directory with Wallet CSV exports")
    watch_dir_parser.add_argument("--api-base-url", default=reader_settings.api_base_url, help="Core API base URL")
    watch_dir_parser.add_argument("--pattern", default="p2p-order-history_*.csv", help="File name pattern")
    watch_dir_parser.add_argument(
        "--poll-interval",
        type=int,
        default=reader_settings.poll_interval_seconds,
        help="How often to check for new files in seconds",
    )

    return parser


def _session_id() -> str:
    return f"{reader_settings.reader_session_prefix}-{uuid4()}"


def run_import_file(input_path: str, api_base_url: str, dry_run: bool) -> int:
    raw_deals = load_wallet_export(input_path)
    payload = normalize_wallet_batch(raw_deals, reader_session_id=_session_id())

    if dry_run:
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    client = WalletApiClient(base_url=api_base_url)
    result = client.import_deals(payload)
    print(
        json.dumps(
            {
                "imported_count": result.imported_count,
                "status_code": result.status_code,
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_normalize_file(input_path: str, output_path: str) -> int:
    raw_deals = load_wallet_export(input_path)
    payload = normalize_wallet_batch(raw_deals, reader_session_id=_session_id())
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output))
    return 0


def run_import_csv(input_path: str, api_base_url: str, dry_run: bool) -> int:
    raw_deals = load_wallet_csv(input_path)
    payload = normalize_wallet_batch(raw_deals, reader_session_id=_session_id())

    if dry_run:
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    result = import_wallet_csv(input_path, api_base_url=api_base_url)
    print(
        json.dumps(
            {
                "imported_count": result.imported_count,
                "status_code": result.status_code,
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_process_csv(input_path: str, api_base_url: str, skip_reconcile: bool) -> int:
    result = import_wallet_csv(input_path, api_base_url=api_base_url)
    output: dict[str, object] = {
        "csv_path": input_path,
        "wallet_imported_count": result.imported_count,
        "wallet_status_code": result.status_code,
    }

    if not skip_reconcile:
        client = CoreApiClient(base_url=api_base_url)
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

    print(json.dumps(output, ensure_ascii=False))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "import-file":
        return run_import_file(args.input, args.api_base_url, args.dry_run)
    if args.command == "normalize-file":
        return run_normalize_file(args.input, args.output)
    if args.command == "import-csv":
        return run_import_csv(args.input, args.api_base_url, args.dry_run)
    if args.command == "process-csv":
        return run_process_csv(args.input, args.api_base_url, args.skip_reconcile)
    if args.command == "watch-csv":
        watch_wallet_csv(args.input, api_base_url=args.api_base_url, poll_interval_seconds=args.poll_interval)
        return 0
    if args.command == "watch-downloads":
        watch_wallet_csv_directory(
            args.directory,
            api_base_url=args.api_base_url,
            pattern=args.pattern,
            poll_interval_seconds=args.poll_interval,
        )
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
