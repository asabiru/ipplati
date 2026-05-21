from __future__ import annotations

import argparse
import json

from app.reader.sync import import_wallet_csv
from app.sync.client import CoreApiClient
from app.telegram_wallet_reader.config import telegram_wallet_reader_settings
from app.telegram_wallet_reader.windows_rpa import (
    capture_telegram_window,
    default_controls_dump_path,
    default_window_screenshot_path,
    dump_telegram_controls,
    find_telegram_windows,
    focus_telegram_window,
    launch_telegram,
    latest_wallet_csv,
    wait_for_new_wallet_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram Desktop Wallet RPA helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-windows", help="List Telegram windows visible on the desktop")
    subparsers.add_parser("focus", help="Bring Telegram window to the foreground")

    launch_parser = subparsers.add_parser("launch", help="Launch Telegram Desktop from the configured path")
    launch_parser.add_argument("--exe-path", default=telegram_wallet_reader_settings.telegram_exe_path, help="Path to Telegram.exe")

    capture_parser = subparsers.add_parser("capture-window", help="Capture a screenshot of the Telegram window")
    capture_parser.add_argument("--output", default=str(default_window_screenshot_path()), help="PNG output path")

    dump_parser = subparsers.add_parser("dump-controls", help="Dump Telegram UI Automation controls tree")
    dump_parser.add_argument("--output", default=str(default_controls_dump_path()), help="Text output path")

    wait_parser = subparsers.add_parser(
        "wait-export",
        help="Wait for a new Wallet CSV export in Downloads after you trigger export manually in Telegram",
    )
    wait_parser.add_argument("--downloads-dir", default=telegram_wallet_reader_settings.downloads_dir, help="Directory to watch")
    wait_parser.add_argument("--pattern", default=telegram_wallet_reader_settings.csv_pattern, help="Wallet CSV filename pattern")
    wait_parser.add_argument("--timeout-seconds", type=int, default=telegram_wallet_reader_settings.wait_timeout_seconds, help="How long to wait")

    import_parser = subparsers.add_parser(
        "manual-export-import",
        help="Focus Telegram, wait for you to export Wallet CSV manually, then import and reconcile automatically",
    )
    import_parser.add_argument("--downloads-dir", default=telegram_wallet_reader_settings.downloads_dir, help="Directory to watch")
    import_parser.add_argument("--pattern", default=telegram_wallet_reader_settings.csv_pattern, help="Wallet CSV filename pattern")
    import_parser.add_argument("--timeout-seconds", type=int, default=telegram_wallet_reader_settings.wait_timeout_seconds, help="How long to wait")
    import_parser.add_argument("--api-base-url", default=telegram_wallet_reader_settings.api_base_url, help="Core API base URL")
    import_parser.add_argument("--skip-reconcile", action="store_true", help="Only import the CSV without running reconciliation")

    return parser


def run_list_windows() -> int:
    windows = [
        {
            "title": window.title,
            "handle": window.handle,
            "left": window.left,
            "top": window.top,
            "right": window.right,
            "bottom": window.bottom,
            "area": window.area,
        }
        for window in find_telegram_windows()
    ]
    print(json.dumps({"windows": windows}, ensure_ascii=False, indent=2))
    return 0


def run_focus() -> int:
    focus_telegram_window()
    print("focused")
    return 0


def run_launch(exe_path: str) -> int:
    pid = launch_telegram(exe_path)
    print(json.dumps({"pid": pid}, ensure_ascii=False))
    return 0


def run_capture_window(output: str) -> int:
    saved = capture_telegram_window(output)
    print(str(saved))
    return 0


def run_dump_controls(output: str) -> int:
    saved = dump_telegram_controls(output)
    print(str(saved))
    return 0


def run_wait_export(downloads_dir: str, pattern: str, timeout_seconds: int) -> int:
    known_latest = latest_wallet_csv(pattern=pattern, downloads_dir=downloads_dir)
    detected = wait_for_new_wallet_csv(
        known_latest=known_latest,
        pattern=pattern,
        downloads_dir=downloads_dir,
        timeout_seconds=timeout_seconds,
    )
    print(str(detected))
    return 0


def run_manual_export_import(
    downloads_dir: str,
    pattern: str,
    timeout_seconds: int,
    api_base_url: str,
    skip_reconcile: bool,
) -> int:
    focus_telegram_window()
    known_latest = latest_wallet_csv(pattern=pattern, downloads_dir=downloads_dir)
    csv_path = wait_for_new_wallet_csv(
        known_latest=known_latest,
        pattern=pattern,
        downloads_dir=downloads_dir,
        timeout_seconds=timeout_seconds,
    )

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
    if args.command == "list-windows":
        return run_list_windows()
    if args.command == "focus":
        return run_focus()
    if args.command == "launch":
        return run_launch(args.exe_path)
    if args.command == "capture-window":
        return run_capture_window(args.output)
    if args.command == "dump-controls":
        return run_dump_controls(args.output)
    if args.command == "wait-export":
        return run_wait_export(args.downloads_dir, args.pattern, args.timeout_seconds)
    if args.command == "manual-export-import":
        return run_manual_export_import(
            args.downloads_dir,
            args.pattern,
            args.timeout_seconds,
            args.api_base_url,
            args.skip_reconcile,
        )
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
