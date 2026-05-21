from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from typing import Any

import httpx


def load_cookies_from_storage_state(storage_state_path: str | Path) -> dict[str, str]:
    state = json.loads(Path(storage_state_path).read_text(encoding="utf-8"))
    cookies: dict[str, str] = {}
    for cookie in state.get("cookies", []):
        if "astralnalog.ru" in cookie.get("domain", ""):
            cookies[cookie["name"]] = cookie["value"]
    return cookies


def fetch_documents_page(
    storage_state_path: str | Path,
    *,
    organization_id: str,
    begin_date: int,
    end_date: int,
    page_number: int = 1,
    count: int = 100,
) -> dict[str, Any]:
    payload = {
        "organizationId": organization_id,
        "order": "desc",
        "orderBy": "dateTime",
        "beginDate": begin_date,
        "endDate": end_date,
        "pageNumber": page_number,
        "count": count,
    }
    cookies = load_cookies_from_storage_state(storage_state_path)
    with httpx.Client(timeout=30, cookies=cookies) as client:
        response = client.post("https://ofd.astralnalog.ru/lk/api/v4.2/documents.tickets", json=payload)
        response.raise_for_status()
        return response.json()


def fetch_all_documents(
    storage_state_path: str | Path,
    *,
    organization_id: str,
    begin_date: int,
    end_date: int,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    first = fetch_documents_page(
        storage_state_path,
        organization_id=organization_id,
        begin_date=begin_date,
        end_date=end_date,
        page_number=1,
        count=page_size,
    )
    result = first.get("result", {})
    total_count = int(result.get("totalCount", 0))
    documents = list(result.get("documents", []))

    if total_count <= len(documents):
        return documents

    total_pages = ceil(total_count / page_size)
    for page_number in range(2, total_pages + 1):
        page = fetch_documents_page(
            storage_state_path,
            organization_id=organization_id,
            begin_date=begin_date,
            end_date=end_date,
            page_number=page_number,
            count=page_size,
        )
        documents.extend(page.get("result", {}).get("documents", []))
    return documents
