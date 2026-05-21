from __future__ import annotations

from base64 import urlsafe_b64decode
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import secrets
import ssl
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AlfaToken, BankTransaction, TransactionDirection


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return urlsafe_b64decode(data + padding)


def decode_jwt_without_verification(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = _urlsafe_b64decode(parts[1]).decode("utf-8")
        return json.loads(payload)
    except Exception:
        return {}


def build_state() -> str:
    nonce = secrets.token_urlsafe(24)
    signature = hmac.new(
        settings.alfa_state_secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def verify_state(state: str | None) -> bool:
    if not state or "." not in state:
        return False
    nonce, signature = state.rsplit(".", 1)
    expected = hmac.new(
        settings.alfa_state_secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def build_authorization_url() -> str:
    if not settings.alfa_client_id:
        raise ValueError("ALFA_CLIENT_ID is not configured")

    params = {
        "response_type": "code",
        "client_id": settings.alfa_client_id,
        "redirect_uri": settings.alfa_redirect_uri,
        "scope": settings.alfa_scope,
        "state": build_state(),
        "nonce": secrets.token_urlsafe(16),
    }
    return f"{settings.alfa_oauth_authorize_url}?{urlencode(params)}"


def _build_ssl_context() -> ssl.SSLContext | bool:
    if settings.alfa_verify_tls:
        if settings.alfa_tls_ca_chain_path:
            context = ssl.create_default_context(cafile=settings.alfa_tls_ca_chain_path)
        else:
            context = ssl.create_default_context()
    else:
        context = ssl._create_unverified_context()

    if settings.alfa_tls_cert_path and settings.alfa_tls_key_path:
        context.load_cert_chain(settings.alfa_tls_cert_path, settings.alfa_tls_key_path)

    return context


def build_http_client() -> httpx.Client:
    return httpx.Client(timeout=30, verify=_build_ssl_context())


def _build_auth_headers() -> dict[str, str]:
    if settings.alfa_api_key:
        return {"Authorization": f"ApiKey {settings.alfa_api_key}"}
    return {}


def save_token(session: Session, payload: dict) -> AlfaToken:
    id_token = payload.get("id_token")
    claims = decode_jwt_without_verification(id_token) if id_token else {}
    subject = claims.get("sub")
    expires_in = payload.get("expires_in")
    expires_at = None
    if expires_in is not None:
        try:
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        except Exception:
            expires_at = None

    token = session.scalar(select(AlfaToken).order_by(AlfaToken.obtained_at.desc()))
    if token is None:
        token = AlfaToken(access_token=payload["access_token"])
        session.add(token)

    token.subject = subject
    token.access_token = payload["access_token"]
    token.refresh_token = payload.get("refresh_token")
    token.id_token = payload.get("id_token")
    token.token_type = payload.get("token_type")
    token.scope = payload.get("scope")
    token.expires_in_minutes = int(expires_in) if expires_in is not None else None
    token.obtained_at = datetime.now(UTC)
    token.expires_at = expires_at
    token.raw_payload = payload
    session.commit()
    session.refresh(token)
    return token


def exchange_authorization_code(code: str, session: Session) -> AlfaToken:
    if not settings.alfa_client_secret:
        raise ValueError("ALFA_CLIENT_SECRET is not configured")

    form_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.alfa_redirect_uri,
        "client_id": settings.alfa_client_id,
        "client_secret": settings.alfa_client_secret,
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
    }
    with build_http_client() as client:
        response = client.post(settings.alfa_oauth_token_url, data=form_data, headers=headers)
        response.raise_for_status()
        payload = response.json()
    return save_token(session, payload)


def refresh_access_token(session: Session) -> AlfaToken:
    token = session.scalar(select(AlfaToken).order_by(AlfaToken.obtained_at.desc()))
    if token is None or not token.refresh_token:
        raise ValueError("No refresh token is stored")
    if not settings.alfa_client_secret:
        raise ValueError("ALFA_CLIENT_SECRET is not configured")

    form_data = {
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
        "client_id": settings.alfa_client_id,
        "client_secret": settings.alfa_client_secret,
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
    }
    with build_http_client() as client:
        response = client.post(settings.alfa_oauth_token_url, data=form_data, headers=headers)
        response.raise_for_status()
        payload = response.json()
    return save_token(session, payload)


def get_current_token(session: Session) -> AlfaToken | None:
    return session.scalar(select(AlfaToken).order_by(AlfaToken.obtained_at.desc()))


def get_valid_access_token(session: Session) -> str:
    token = get_current_token(session)
    if token is None:
        raise ValueError("No Alfa token is stored")

    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at and expires_at <= datetime.now(UTC) + timedelta(minutes=2):
        token = refresh_access_token(session)
    return token.access_token


def _get_auth_headers(session: Session | None) -> dict[str, str]:
    if settings.alfa_api_key:
        return {
            "Authorization": f"ApiKey {settings.alfa_api_key}",
            "Accept": "application/json",
        }
    if session is None:
        raise ValueError("DB session is required for OAuth token auth")
    access_token = get_valid_access_token(session)
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _parse_direction(value: str | None) -> TransactionDirection:
    normalized = (value or "").strip().upper()
    if normalized == "DEBIT":
        return TransactionDirection.outgoing
    if normalized == "CREDIT":
        return TransactionDirection.incoming
    return TransactionDirection.incoming


def _mask_account(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 4:
        return value
    return f"*{digits[-4:]}"


def _normalize_currency(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"RUR", "RUB", "643"}:
        return "RUB"
    return normalized or "RUB"


def _extract_amount(transaction: dict) -> Decimal:
    amount = transaction.get("amount") or {}
    raw = amount.get("amount") or transaction.get("amountRub", {}).get("amount")
    if raw is None:
        raise ValueError(
            f"Transaction {transaction.get('uuid') or transaction.get('transactionId')} has no amount"
        )
    return Decimal(str(raw))


def _extract_booked_at(transaction: dict) -> datetime:
    raw = transaction.get("operationDate") or transaction.get("documentDate")
    if raw is None:
        raise ValueError(
            f"Transaction {transaction.get('uuid') or transaction.get('transactionId')} has no date"
        )
    if "T" in raw:
        return datetime.fromisoformat(raw)
    return datetime.fromisoformat(f"{raw}T00:00:00")


def _map_statement_transaction(transaction: dict) -> dict:
    transfer = transaction.get("rurTransfer") or {}
    direction = _parse_direction(transaction.get("direction"))

    if direction == TransactionDirection.incoming:
        payer_name = transfer.get("payerName") or transfer.get("payeeName")
        payer_account = transfer.get("payerAccount") or transaction.get("correspondingAccount")
    else:
        payer_name = transfer.get("payeeName") or transfer.get("payerName")
        payer_account = transfer.get("payeeAccount") or transaction.get("correspondingAccount")

    return {
        "external_id": str(
            transaction.get("uuid")
            or transaction.get("transactionId")
            or transaction.get("operationId")
            or secrets.token_hex(8)
        ),
        "amount": _extract_amount(transaction),
        "currency": _normalize_currency((transaction.get("amount") or {}).get("currencyName")),
        "direction": direction,
        "payer_name": payer_name,
        "payer_account_masked": _mask_account(payer_account),
        "reference": transaction.get("paymentPurpose") or transaction.get("number"),
        "booked_at": _extract_booked_at(transaction),
        "raw_payload": transaction,
    }


def fetch_statement_transactions(session: Session, account_number: str, statement_date: str, page: int = 1) -> dict:
    headers = _get_auth_headers(session)
    params = {
        "accountNumber": account_number,
        "statementDate": statement_date,
        "page": str(page),
    }
    with build_http_client() as client:
        response = client.get(settings.alfa_statement_transactions_url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


def _statement_has_next_page(payload: dict) -> bool:
    links = payload.get("_links") or payload.get("links") or []
    if isinstance(links, dict):
        next_link = links.get("next")
        return bool(next_link)
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            if str(item.get("rel") or "").lower() == "next":
                return True
    return False


def fetch_statement_transactions_all_pages(
    session: Session,
    *,
    account_number: str,
    statement_date: str,
    start_page: int = 1,
) -> tuple[list[dict], list[dict]]:
    page = start_page
    page_payloads: list[dict] = []
    transactions: list[dict] = []

    while True:
        payload = fetch_statement_transactions(
            session,
            account_number=account_number,
            statement_date=statement_date,
            page=page,
        )
        page_payloads.append(payload)
        current_transactions = payload.get("transactions") or []
        transactions.extend(current_transactions)

        if not current_transactions:
            break
        if not _statement_has_next_page(payload):
            break
        page += 1

    return transactions, page_payloads


def import_statement_transactions(
    session: Session,
    account_number: str,
    statement_date: str,
    page: int = 1,
) -> tuple[int, dict]:
    payload = fetch_statement_transactions(session, account_number=account_number, statement_date=statement_date, page=page)
    transactions = payload.get("transactions") or []
    imported = 0

    for raw in transactions:
        mapped = _map_statement_transaction(raw)
        transaction = session.scalar(select(BankTransaction).where(BankTransaction.external_id == mapped["external_id"]))
        if transaction is None:
            transaction = BankTransaction(external_id=mapped["external_id"])
            session.add(transaction)
            imported += 1

        transaction.provider = "alfa"
        transaction.amount = mapped["amount"]
        transaction.currency = mapped["currency"]
        transaction.direction = mapped["direction"]
        transaction.payer_name = mapped["payer_name"]
        transaction.payer_account_masked = mapped["payer_account_masked"]
        transaction.reference = mapped["reference"]
        transaction.booked_at = mapped["booked_at"]
        transaction.raw_payload = raw

    session.commit()
    return imported, payload


def import_statement_transactions_range(
    session: Session,
    *,
    account_number: str,
    date_from: date,
    date_to: date,
    all_pages: bool = True,
    start_page: int = 1,
) -> tuple[int, int, int, dict]:
    if date_from > date_to:
        raise ValueError("date_from must be less than or equal to date_to")

    imported = 0
    total_transactions = 0
    pages_processed = 0
    raw_days: list[dict] = []

    current = date_from
    while current <= date_to:
        statement_date = current.isoformat()

        if all_pages:
            day_transactions, page_payloads = fetch_statement_transactions_all_pages(
                session,
                account_number=account_number,
                statement_date=statement_date,
                start_page=start_page,
            )
            pages_processed += len(page_payloads)
            raw_days.append(
                {
                    "statement_date": statement_date,
                    "pages": page_payloads,
                }
            )
            transactions = day_transactions
        else:
            payload = fetch_statement_transactions(
                session,
                account_number=account_number,
                statement_date=statement_date,
                page=start_page,
            )
            pages_processed += 1
            raw_days.append(
                {
                    "statement_date": statement_date,
                    "pages": [payload],
                }
            )
            transactions = payload.get("transactions") or []

        total_transactions += len(transactions)
        for raw in transactions:
            mapped = _map_statement_transaction(raw)
            transaction = session.scalar(select(BankTransaction).where(BankTransaction.external_id == mapped["external_id"]))
            if transaction is None:
                transaction = BankTransaction(external_id=mapped["external_id"])
                session.add(transaction)
                imported += 1

            transaction.provider = "alfa"
            transaction.amount = mapped["amount"]
            transaction.currency = mapped["currency"]
            transaction.direction = mapped["direction"]
            transaction.payer_name = mapped["payer_name"]
            transaction.payer_account_masked = mapped["payer_account_masked"]
            transaction.reference = mapped["reference"]
            transaction.booked_at = mapped["booked_at"]
            transaction.raw_payload = raw

        current += timedelta(days=1)

    session.commit()
    return imported, total_transactions, pages_processed, {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": raw_days,
    }
