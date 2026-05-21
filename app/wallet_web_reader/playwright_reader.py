from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Download, Page, TimeoutError, sync_playwright

from app.wallet_web_reader.config import wallet_web_reader_settings


def _ensure_parent(path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _ensure_dir(path: str | Path) -> Path:
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    return output


def _artifact_path(filename: str) -> Path:
    base = _ensure_dir(wallet_web_reader_settings.artifacts_dir)
    return base / filename


def _save_debug(page: Page, prefix: str) -> None:
    html_path = _artifact_path(f"{prefix}.html")
    html_path.write_text(page.content(), encoding="utf-8")
    if wallet_web_reader_settings.screenshot_on_error:
        page.screenshot(path=str(_artifact_path(f"{prefix}.png")), full_page=True)


def _selector_candidates(raw: str) -> list[str]:
    return [item.strip() for item in raw.split("||") if item.strip()]


def _click_first_available(page: Page, selectors: str, timeout_ms: int = 5000) -> bool:
    for selector in _selector_candidates(selectors):
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        try:
            locator.first.click(timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


@contextmanager
def browser_context(storage_state_path: str | None = None) -> Iterator[BrowserContext]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=wallet_web_reader_settings.headless,
        slow_mo=wallet_web_reader_settings.slow_mo_ms,
        channel=wallet_web_reader_settings.browser_channel or None,
    )
    context = browser.new_context(
        accept_downloads=True,
        storage_state=storage_state_path if storage_state_path and Path(storage_state_path).exists() else None,
    )
    try:
        yield context
    finally:
        context.close()
        browser.close()
        playwright.stop()


def wait_for_manual_login(storage_state_path: str | None = None, timeout_ms: int = 600000) -> Path:
    target_path = _ensure_parent(storage_state_path or wallet_web_reader_settings.storage_state_path)
    with browser_context() as context:
        page = context.new_page()
        try:
            page.goto(wallet_web_reader_settings.orders_url, wait_until="domcontentloaded")
            page.wait_for_url(wallet_web_reader_settings.login_wait_url_pattern, timeout=timeout_ms)
            page.wait_for_load_state("networkidle")
            context.storage_state(path=str(target_path))
            return target_path
        except Exception:
            _save_debug(page, "wallet-manual-login-error")
            raise


def _open_orders_page(page: Page) -> None:
    page.goto(wallet_web_reader_settings.orders_url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector(wallet_web_reader_settings.orders_ready_selector, timeout=30000)
    except TimeoutError:
        pass

    # If the configured orders URL lands on a shell page, try common nav entries.
    _click_first_available(page, wallet_web_reader_settings.history_nav_selectors, timeout_ms=4000)
    page.wait_for_load_state("networkidle")


def _save_download(download: Download, downloads_dir: str | Path) -> Path:
    target_dir = _ensure_dir(downloads_dir)
    suggested_name = download.suggested_filename or "wallet-export.csv"
    output_path = target_dir / suggested_name
    download.save_as(str(output_path))
    return output_path


def download_wallet_csv(
    storage_state_path: str | None = None,
    *,
    downloads_dir: str | None = None,
    timeout_ms: int = 120000,
) -> Path:
    storage_state = storage_state_path or wallet_web_reader_settings.storage_state_path
    target_dir = downloads_dir or wallet_web_reader_settings.downloads_dir

    with browser_context(storage_state) as context:
        page = context.new_page()
        try:
            _open_orders_page(page)

            with page.expect_download(timeout=timeout_ms) as download_info:
                clicked_export = _click_first_available(page, wallet_web_reader_settings.export_button_selectors, timeout_ms=10000)
                if not clicked_export:
                    raise RuntimeError("Wallet export button was not found")

                clicked_csv = _click_first_available(page, wallet_web_reader_settings.export_csv_selectors, timeout_ms=5000)
                if clicked_csv:
                    page.wait_for_load_state("networkidle")

            download = download_info.value
            output_path = _save_download(download, target_dir)

            meta = {
                "url": page.url,
                "downloaded_file": str(output_path),
                "suggested_filename": download.suggested_filename,
            }
            _artifact_path("last-download.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return output_path
        except Exception:
            _save_debug(page, "wallet-download-error")
            raise
