from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from app.ofd_reader.config import ofd_reader_settings


def _ensure_parent(path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _artifact_path(filename: str) -> Path:
    base = Path(ofd_reader_settings.artifacts_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / filename


def _save_debug(page: Page, prefix: str) -> None:
    html_path = _artifact_path(f"{prefix}.html")
    html_path.write_text(page.content(), encoding="utf-8")
    if ofd_reader_settings.screenshot_on_error:
        page.screenshot(path=str(_artifact_path(f"{prefix}.png")), full_page=True)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 11 and digits.startswith("7"):
        return digits[1:]
    return digits


def _type_into(locator, value: str) -> None:
    locator.click()
    locator.press("Control+A")
    locator.press("Delete")
    locator.type(value, delay=60)


@contextmanager
def browser_context(storage_state_path: str | None = None) -> Iterator[BrowserContext]:
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=ofd_reader_settings.headless,
        slow_mo=ofd_reader_settings.slow_mo_ms,
        channel=ofd_reader_settings.browser_channel or None,
    )
    context = browser.new_context(
        storage_state=storage_state_path if storage_state_path and Path(storage_state_path).exists() else None
    )
    try:
        yield context
    finally:
        context.close()
        browser.close()
        playwright.stop()


def login_and_save_state(storage_state_path: str | None = None) -> Path:
    target_path = _ensure_parent(storage_state_path or ofd_reader_settings.storage_state_path)
    with browser_context() as context:
        page = context.new_page()
        try:
            page.goto(ofd_reader_settings.login_url, wait_until="domcontentloaded")

            cookie_button = page.locator(ofd_reader_settings.cookie_accept_selector)
            if cookie_button.count() > 0:
                try:
                    cookie_button.first.click(timeout=3000)
                except Exception:
                    pass

            phone_locator = page.locator(ofd_reader_settings.phone_selector)
            password_locator = page.locator(ofd_reader_settings.password_selector)

            if phone_locator.count() == 0 or password_locator.count() == 0:
                entry_button = page.locator(ofd_reader_settings.login_entry_selector)
                if entry_button.count() > 0:
                    entry_button.first.click(timeout=10000)
                    page.wait_for_load_state("networkidle")

                existing_account = page.locator(ofd_reader_settings.existing_account_selector)
                page.wait_for_load_state("domcontentloaded")
                if "signUp" in page.url and existing_account.count() > 0:
                    existing_account.last.click(timeout=10000)
                    page.wait_for_load_state("networkidle")

                phone_locator = page.locator(ofd_reader_settings.phone_selector)
                password_locator = page.locator(ofd_reader_settings.password_selector)

            if ofd_reader_settings.username:
                username_locator = page.locator(ofd_reader_settings.username_selector)
                if phone_locator.count() > 0:
                    phone_locator.first.wait_for(state="visible", timeout=30000)
                if phone_locator.count() > 0:
                    _type_into(phone_locator.first, _normalize_phone(ofd_reader_settings.username))
                elif username_locator.count() > 0:
                    username_locator.first.wait_for(state="visible", timeout=30000)
                    _type_into(username_locator.first, ofd_reader_settings.username)
            if ofd_reader_settings.password:
                password_locator.first.wait_for(state="visible", timeout=30000)
                _type_into(password_locator.first, ofd_reader_settings.password)
            if ofd_reader_settings.username and ofd_reader_settings.password:
                page.locator(ofd_reader_settings.submit_selector).first.click()

            page.wait_for_load_state("networkidle")
            page.goto(ofd_reader_settings.dashboard_url, wait_until="domcontentloaded")
            page.wait_for_url("**/lk/**", timeout=120000)
            context.storage_state(path=str(target_path))
            return target_path
        except Exception:
            _save_debug(page, "ofd-login-error")
            raise


def _navigate_to_checks(page: Page) -> None:
    page.goto(ofd_reader_settings.dashboard_url, wait_until="domcontentloaded")
    try:
        page.locator(ofd_reader_settings.checks_nav_selector).first.click(timeout=10000)
    except Exception:
        page.goto(ofd_reader_settings.checks_url, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")


def _extract_best_table(page: Page) -> list[dict[str, Any]]:
    js = """
    (tableSelector) => {
      const tables = Array.from(document.querySelectorAll(tableSelector));
      const normalized = tables.map((table, tableIndex) => {
        const headers = Array.from(table.querySelectorAll('thead th')).map((th, i) => th.innerText.trim() || `column_${i + 1}`);
        const bodyRows = Array.from(table.querySelectorAll('tbody tr'));
        const rows = bodyRows.map((tr, rowIndex) => {
          const cells = Array.from(tr.querySelectorAll('td'));
          const links = Array.from(tr.querySelectorAll('a')).map((a) => ({ text: a.innerText.trim(), href: a.href }));
          const row = {
            _table_index: tableIndex,
            _row_index: rowIndex,
            _cells: cells.map((td) => td.innerText.trim()),
            _links: links,
          };
          cells.forEach((td, i) => {
            const header = headers[i] || `column_${i + 1}`;
            row[header] = td.innerText.trim();
          });
          return row;
        });
        return { tableIndex, headers, rows };
      });
      normalized.sort((a, b) => b.rows.length - a.rows.length);
      return normalized[0] || { tableIndex: 0, headers: [], rows: [] };
    }
    """
    result = page.evaluate(js, ofd_reader_settings.table_selector)
    return result.get("rows", [])


def scrape_checks_rows(storage_state_path: str | None = None, max_pages: int | None = None) -> list[dict[str, Any]]:
    with browser_context(storage_state_path or ofd_reader_settings.storage_state_path) as context:
        page = context.new_page()
        rows: list[dict[str, Any]] = []
        try:
            _navigate_to_checks(page)
            limit = max_pages or ofd_reader_settings.max_pages

            for _ in range(limit):
                page.wait_for_selector(ofd_reader_settings.table_selector, timeout=30000)
                rows.extend(_extract_best_table(page))

                next_locator = page.locator(ofd_reader_settings.next_page_selector)
                if next_locator.count() == 0:
                    break
                next_button = next_locator.first
                if next_button.is_disabled():
                    break
                next_button.click()
                page.wait_for_load_state("networkidle")

            return rows
        except Exception:
            _save_debug(page, "ofd-scrape-error")
            raise


def save_rows_json(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    output = _ensure_parent(output_path)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def wait_for_manual_login(storage_state_path: str | None = None, timeout_ms: int = 600000) -> Path:
    target_path = _ensure_parent(storage_state_path or ofd_reader_settings.storage_state_path)
    with browser_context() as context:
        page = context.new_page()
        try:
            page.goto(ofd_reader_settings.dashboard_url, wait_until="domcontentloaded")
            page.wait_for_url("**/lk/**", timeout=timeout_ms)
            context.storage_state(path=str(target_path))
            return target_path
        except Exception:
            _save_debug(page, "ofd-manual-login-error")
            raise
