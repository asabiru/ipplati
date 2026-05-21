from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from app.ofd_reader.api_reader import fetch_all_documents
from app.ofd_reader.client import OfdApiClient
from app.ofd_reader.config import ofd_reader_settings
from app.ofd_reader.normalizer import normalize_ofd_api_documents, normalize_ofd_rows
from app.ofd_reader.playwright_reader import (
    login_and_save_state,
    save_rows_json,
    scrape_checks_rows,
    wait_for_manual_login,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Astral OFD Playwright reader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Open browser, log in, and save storage state")
    login_parser.add_argument("--storage-state", default=ofd_reader_settings.storage_state_path, help="Path to state JSON")

    manual_login_parser = subparsers.add_parser(
        "manual-login",
        help="Open browser and wait until you complete Astral login manually",
    )
    manual_login_parser.add_argument(
        "--storage-state",
        default=ofd_reader_settings.storage_state_path,
        help="Path to state JSON",
    )
    manual_login_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="How long to wait for manual login completion",
    )

    scrape_parser = subparsers.add_parser("scrape", help="Scrape checks table rows from Astral OFD")
    scrape_parser.add_argument("--storage-state", default=ofd_reader_settings.storage_state_path, help="Path to state JSON")
    scrape_parser.add_argument("--output", required=True, help="Path to save scraped rows JSON")
    scrape_parser.add_argument("--max-pages", type=int, default=ofd_reader_settings.max_pages, help="Max pages to read")

    fetch_api_parser = subparsers.add_parser("fetch-api", help="Fetch OFD documents through the authenticated web API")
    fetch_api_parser.add_argument("--storage-state", default=ofd_reader_settings.storage_state_path, help="Path to state JSON")
    fetch_api_parser.add_argument("--output", required=True, help="Path to save fetched OFD documents JSON")
    fetch_api_parser.add_argument("--date-from", required=True, help="Start date in YYYY-MM-DD")
    fetch_api_parser.add_argument("--date-to", required=True, help="End date in YYYY-MM-DD")

    import_parser = subparsers.add_parser("import", help="Scrape OFD checks and import them into the core API")
    import_parser.add_argument("--storage-state", default=ofd_reader_settings.storage_state_path, help="Path to state JSON")
    import_parser.add_argument("--api-base-url", default=ofd_reader_settings.api_base_url, help="Core API base URL")
    import_parser.add_argument("--max-pages", type=int, default=ofd_reader_settings.max_pages, help="Max pages to read")
    import_parser.add_argument("--dry-run", action="store_true", help="Print normalized receipts without sending them")
    import_parser.add_argument("--dump-rows", help="Optional path to save raw scraped rows JSON")

    import_api_parser = subparsers.add_parser(
        "import-api",
        help="Fetch OFD documents through the authenticated web API and import sale/refund receipts into the core API",
    )
    import_api_parser.add_argument("--storage-state", default=ofd_reader_settings.storage_state_path, help="Path to state JSON")
    import_api_parser.add_argument("--api-base-url", default=ofd_reader_settings.api_base_url, help="Core API base URL")
    import_api_parser.add_argument("--date-from", required=True, help="Start date in YYYY-MM-DD")
    import_api_parser.add_argument("--date-to", required=True, help="End date in YYYY-MM-DD")
    import_api_parser.add_argument("--dry-run", action="store_true", help="Print normalized receipts without sending them")
    import_api_parser.add_argument("--dump-documents", help="Optional path to save raw API documents JSON")

    return parser


def run_login(storage_state: str) -> int:
    saved = login_and_save_state(storage_state)
    print(str(saved))
    return 0


def run_manual_login(storage_state: str, timeout_seconds: int) -> int:
    saved = wait_for_manual_login(storage_state, timeout_ms=timeout_seconds * 1000)
    print(str(saved))
    return 0


def run_scrape(storage_state: str, output: str, max_pages: int) -> int:
    rows = scrape_checks_rows(storage_state_path=storage_state, max_pages=max_pages)
    saved = save_rows_json(rows, output)
    print(json.dumps({"rows": len(rows), "output": str(saved)}, ensure_ascii=False))
    return 0


def _date_to_begin_timestamp(value: str) -> int:
    dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    return int(dt.timestamp())


def _date_to_end_timestamp(value: str) -> int:
    dt = datetime.combine(date.fromisoformat(value), time.max, tzinfo=timezone.utc)
    return int(dt.timestamp())


def run_fetch_api(storage_state: str, output: str, date_from: str, date_to: str) -> int:
    documents = fetch_all_documents(
        storage_state,
        organization_id=ofd_reader_settings.organization_id,
        begin_date=_date_to_begin_timestamp(date_from),
        end_date=_date_to_end_timestamp(date_to),
        page_size=ofd_reader_settings.api_page_size,
    )
    saved = save_rows_json(documents, output)
    print(json.dumps({"documents": len(documents), "output": str(saved)}, ensure_ascii=False))
    return 0


def run_import(storage_state: str, api_base_url: str, max_pages: int, dry_run: bool, dump_rows: str | None) -> int:
    rows = scrape_checks_rows(storage_state_path=storage_state, max_pages=max_pages)
    if dump_rows:
        save_rows_json(rows, dump_rows)

    payload = normalize_ofd_rows(rows)
    if dry_run:
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    client = OfdApiClient(base_url=api_base_url)
    result = client.import_receipts(payload)
    print(
        json.dumps(
            {
                "scraped_rows": len(rows),
                "normalized_receipts": len(payload.receipts),
                "imported_count": result.imported_count,
                "status_code": result.status_code,
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_import_api(
    storage_state: str,
    api_base_url: str,
    date_from: str,
    date_to: str,
    dry_run: bool,
    dump_documents: str | None,
) -> int:
    documents = fetch_all_documents(
        storage_state,
        organization_id=ofd_reader_settings.organization_id,
        begin_date=_date_to_begin_timestamp(date_from),
        end_date=_date_to_end_timestamp(date_to),
        page_size=ofd_reader_settings.api_page_size,
    )
    if dump_documents:
        save_rows_json(documents, dump_documents)

    payload = normalize_ofd_api_documents(documents)
    skipped_non_receipt_documents = len(documents) - len(payload.receipts)
    if dry_run:
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    client = OfdApiClient(base_url=api_base_url)
    result = client.import_receipts(payload)
    print(
        json.dumps(
            {
                "fetched_documents": len(documents),
                "normalized_receipts": len(payload.receipts),
                "skipped_non_receipt_documents": skipped_non_receipt_documents,
                "imported_count": result.imported_count,
                "status_code": result.status_code,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "login":
        return run_login(args.storage_state)
    if args.command == "manual-login":
        return run_manual_login(args.storage_state, args.timeout_seconds)
    if args.command == "scrape":
        return run_scrape(args.storage_state, args.output, args.max_pages)
    if args.command == "fetch-api":
        return run_fetch_api(args.storage_state, args.output, args.date_from, args.date_to)
    if args.command == "import":
        return run_import(args.storage_state, args.api_base_url, args.max_pages, args.dry_run, args.dump_rows)
    if args.command == "import-api":
        return run_import_api(
            args.storage_state,
            args.api_base_url,
            args.date_from,
            args.date_to,
            args.dry_run,
            args.dump_documents,
        )

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
