from datetime import UTC, date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import BankTransaction, Deal, DealStatus, FiscalReceipt, ReceiptType
from app.schemas import (
    BankTransactionOut,
    BankTransactionBatchIn,
    DealOut,
    ManualDealCreateIn,
    ManualDealCreateOut,
    MatchRunResult,
    ReceiptBatchIn,
    ReconciliationSummary,
    TelegramDashboardSnapshot,
    WalletDealBatchIn,
)
from app.schemas_sber import (
    SberCallbackPayload,
    SberStatementPullIn,
    SberStatementPullOut,
    SberStatusOut,
    SberTokenOut,
    SberUserInfoOut,
)
from app.services.classification import classify_all_deals, classify_deal_status, link_bank_return_transactions
from app.services.auto_sync import auto_sync_recent_sber
from app.services.matching import run_matching
from app.services.receipt_matching import reconcile_receipts
from app.services.reporting import build_dashboard_snapshot, build_reconciliation_summary
from app.services.sber_auth import (
    build_authorization_url,
    exchange_authorization_code,
    fetch_user_info,
    import_statement_transactions,
    import_statement_transactions_range,
    get_current_token,
    refresh_access_token,
    verify_state,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "public_base_url": settings.public_base_url,
    }


@router.get("/sber/connect")
def sber_connect() -> RedirectResponse:
    try:
        auth_url = build_authorization_url()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(auth_url)


@router.get("/sber/status", response_model=SberStatusOut)
def sber_status(session: Session = Depends(get_session)) -> SberStatusOut:
    token = get_current_token(session)
    token_out = None
    if token is not None:
        token_out = SberTokenOut(
            subject=token.subject,
            token_type=token.token_type,
            scope=token.scope,
            expires_at=token.expires_at.isoformat() if token.expires_at else None,
            obtained_at=token.obtained_at.isoformat() if token.obtained_at else None,
        )
    return SberStatusOut(
        configured=bool(settings.sber_client_id and settings.sber_redirect_uri),
        redirect_uri=settings.sber_redirect_uri,
        authorize_url=settings.sber_oauth_authorize_url,
        token_url=settings.sber_oauth_token_url,
        scope=settings.sber_scope,
        has_client_secret=bool(settings.sber_client_secret),
        has_client_certificate=bool(
            settings.sber_tls_p12_path or (settings.sber_tls_cert_path and settings.sber_tls_key_path)
        ),
        token=token_out,
    )


@router.get("/sber/callback")
def sber_callback(
    request: Request,
    session: Session = Depends(get_session),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> dict:
    payload = SberCallbackPayload(
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )
    token_info = None
    state_valid = verify_state(state) if state else False
    exchange_error = None
    if code and settings.sber_client_secret:
        try:
            token = exchange_authorization_code(code, session)
            token_info = {
                "subject": token.subject,
                "obtained_at": token.obtained_at.astimezone(UTC).isoformat(),
                "expires_at": token.expires_at.astimezone(UTC).isoformat() if token.expires_at else None,
                "scope": token.scope,
            }
        except Exception as exc:
            exchange_error = str(exc)
    return {
        "status": "received",
        "callback_url": str(request.url),
        "state_valid": state_valid,
        "payload": payload.model_dump(),
        "token": token_info,
        "exchange_error": exchange_error,
        "message": "Sber callback received.",
    }


@router.post("/sber/refresh")
def sber_refresh(session: Session = Depends(get_session)) -> dict:
    try:
        token = refresh_access_token(session)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "refreshed",
        "subject": token.subject,
        "obtained_at": token.obtained_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "scope": token.scope,
    }


@router.get("/sber/user-info", response_model=SberUserInfoOut)
def sber_user_info(session: Session = Depends(get_session)) -> SberUserInfoOut:
    try:
        payload = fetch_user_info(session)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SberUserInfoOut(raw=payload)


@router.post("/sber/statements/pull", response_model=SberStatementPullOut)
def sber_pull_statement(
    payload: SberStatementPullIn,
    session: Session = Depends(get_session),
) -> SberStatementPullOut:
    account_number = payload.account_number or settings.sber_default_account_number
    if not account_number:
        raise HTTPException(status_code=400, detail="account_number is required")

    statement_date = payload.statement_date
    date_from = payload.date_from
    date_to = payload.date_to
    if statement_date is None and (date_from is None or date_to is None):
        raise HTTPException(status_code=400, detail="statement_date or date_from/date_to is required")
    if statement_date is not None and (date_from is not None or date_to is not None):
        raise HTTPException(status_code=400, detail="Use either statement_date or date_from/date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be less than or equal to date_to")

    try:
        if statement_date is not None:
            imported, raw = import_statement_transactions(
                session,
                account_number=account_number,
                statement_date=statement_date.isoformat(),
                page=payload.page,
            )
            transactions = raw.get("transactions") or []
            return SberStatementPullOut(
                account_number=account_number,
                statement_date=statement_date,
                date_from=statement_date,
                date_to=statement_date,
                page=payload.page,
                pages_processed=1,
                days_processed=1,
                imported=imported,
                total_transactions=len(transactions),
                raw=raw,
            )

        imported, total_transactions, pages_processed, raw = import_statement_transactions_range(
            session,
            account_number=account_number,
            date_from=date_from,
            date_to=date_to,
            all_pages=payload.all_pages,
            start_page=payload.page,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SberStatementPullOut(
        account_number=account_number,
        statement_date=None,
        date_from=date_from,
        date_to=date_to,
        page=payload.page,
        pages_processed=pages_processed,
        days_processed=(date_to - date_from).days + 1 if date_from and date_to else 0,
        imported=imported,
        total_transactions=total_transactions,
        raw=raw,
    )


@router.post("/wallet/deals/import", response_model=list[DealOut])
def import_wallet_deals(payload: WalletDealBatchIn, session: Session = Depends(get_session)) -> list[Deal]:
    stored: list[Deal] = []
    for incoming in payload.deals:
        deal = session.scalar(select(Deal).where(Deal.external_id == incoming.external_id))
        if deal is None:
            deal = Deal(external_id=incoming.external_id)
            session.add(deal)

        deal.source = "wallet"
        deal.status = incoming.status
        deal.expected_fiat_amount = incoming.expected_fiat_amount
        deal.fiat_currency = incoming.fiat_currency
        deal.counterparty_name = incoming.counterparty_name
        deal.counterparty_wallet_id = incoming.counterparty_wallet_id
        deal.payment_method = incoming.payment_method
        deal.opened_at = incoming.opened_at
        deal.completed_at = incoming.completed_at
        deal.return_reason = incoming.raw_payload.get("return_reason")
        deal.notes = incoming.notes
        deal.raw_payload = incoming.raw_payload
        classify_deal_status(deal)
        stored.append(deal)

    session.commit()
    for deal in stored:
        session.refresh(deal)
    return stored


@router.post("/bank/transactions/import")
def import_bank_transactions(
    payload: BankTransactionBatchIn,
    session: Session = Depends(get_session),
) -> dict[str, int]:
    imported = 0
    for incoming in payload.transactions:
        transaction = session.scalar(select(BankTransaction).where(BankTransaction.external_id == incoming.external_id))
        if transaction is None:
            transaction = BankTransaction(external_id=incoming.external_id)
            session.add(transaction)
            imported += 1

        transaction.provider = "sber"
        transaction.amount = incoming.amount
        transaction.currency = incoming.currency
        transaction.direction = incoming.direction
        transaction.payer_name = incoming.payer_name
        transaction.payer_account_masked = incoming.payer_account_masked
        transaction.reference = incoming.reference
        transaction.booked_at = incoming.booked_at
        transaction.raw_payload = incoming.raw_payload

        related_deal_external_id = incoming.raw_payload.get("related_deal_external_id")
        if incoming.direction.value == "outgoing" and related_deal_external_id:
            deal = session.scalar(select(Deal).where(Deal.external_id == related_deal_external_id))
            if deal is not None:
                transaction.deal_id = deal.id
                deal.status = DealStatus.return_detected
                deal.return_reason = str(incoming.raw_payload.get("return_reason") or "bank_outgoing")
                classify_deal_status(deal)

    session.commit()
    bank_returns_linked = link_bank_return_transactions(session)
    return {"imported": imported, "bank_returns_linked": bank_returns_linked}


@router.get("/bank/transactions/unmatched", response_model=list[BankTransactionOut])
def list_unmatched_bank_transactions(
    session: Session = Depends(get_session),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[BankTransaction]:
    return session.scalars(
        select(BankTransaction)
        .where(BankTransaction.deal_id.is_(None))
        .order_by(BankTransaction.booked_at.desc())
        .limit(limit)
    ).all()


@router.post("/manual/deals", response_model=ManualDealCreateOut)
def create_manual_deal(
    payload: ManualDealCreateIn,
    session: Session = Depends(get_session),
) -> ManualDealCreateOut:
    transaction = session.scalar(
        select(BankTransaction).where(BankTransaction.external_id == payload.bank_transaction_external_id)
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Bank transaction not found")

    existing_deal = transaction.deal
    if existing_deal is not None and existing_deal.source != "manual":
        raise HTTPException(status_code=400, detail="Bank transaction is already linked to a non-manual deal")

    side = payload.side.lower()
    created = existing_deal is None
    deal = existing_deal
    if deal is None:
        deal = Deal(
            external_id=f"MANUAL-{transaction.external_id}",
            source="manual",
        )
        session.add(deal)

    fiat_amount = Decimal(transaction.amount)
    crypto_amount = (fiat_amount / payload.rate) if payload.rate > 0 else Decimal("0")
    counterparty_name = payload.counterparty_name or transaction.payer_name

    deal.source = "manual"
    deal.status = DealStatus.matched
    deal.expected_fiat_amount = fiat_amount
    deal.fiat_currency = transaction.currency
    deal.counterparty_name = counterparty_name
    deal.counterparty_wallet_id = None
    deal.payment_method = payload.payment_method
    deal.opened_at = transaction.booked_at
    deal.completed_at = transaction.booked_at
    deal.return_reason = None
    deal.return_detected_at = None
    deal.notes = payload.comment
    deal.raw_payload = {
        "source": "manual",
        "side": side,
        "price": str(payload.rate),
        "fiat_amount": str(fiat_amount),
        "net_crypto_amount": str(crypto_amount),
        "paid_fee_crypto_amount": "0",
        "crypto_currency": payload.crypto_currency,
        "counterparty_name": counterparty_name,
        "bank_transaction_external_id": transaction.external_id,
        "bank_direction": transaction.direction.value,
        "manual_comment": payload.comment,
    }

    transaction.deal = deal
    classify_deal_status(deal)
    session.commit()
    session.refresh(deal)
    session.refresh(transaction)
    return ManualDealCreateOut(
        deal=DealOut.model_validate(deal),
        bank_transaction=BankTransactionOut.model_validate(transaction),
        created=created,
    )


@router.post("/receipts/import")
def import_receipts(payload: ReceiptBatchIn, session: Session = Depends(get_session)) -> dict[str, int]:
    imported = 0
    for incoming in payload.receipts:
        deal = None
        if incoming.deal_external_id:
            deal = session.scalar(select(Deal).where(Deal.external_id == incoming.deal_external_id))
            if deal is None:
                raise HTTPException(status_code=404, detail=f"Deal {incoming.deal_external_id} not found")

        receipt = session.scalar(select(FiscalReceipt).where(FiscalReceipt.external_id == incoming.external_id))
        if receipt is None:
            receipt = FiscalReceipt(external_id=incoming.external_id)
            session.add(receipt)
            imported += 1

        receipt.provider = "astral"
        receipt.receipt_type = incoming.receipt_type
        receipt.status = incoming.status
        receipt.amount = incoming.amount
        receipt.issued_at = incoming.issued_at
        receipt.deal_id = deal.id if deal is not None else None
        receipt.raw_payload = incoming.raw_payload

        if deal is not None and incoming.receipt_type == ReceiptType.refund:
            deal.status = DealStatus.return_detected
            deal.return_reason = str(incoming.raw_payload.get("return_reason") or "refund_receipt")
        elif (
            deal is not None
            and incoming.receipt_type == ReceiptType.sale
            and incoming.status.value == "issued"
            and deal.status == DealStatus.matched
        ):
            deal.status = DealStatus.receipted

        if deal is not None:
            classify_deal_status(deal)

    session.commit()
    return {"imported": imported}


@router.post("/matching/run", response_model=list[MatchRunResult])
def run_matching_endpoint(session: Session = Depends(get_session)) -> list[MatchRunResult]:
    return run_matching(session)


@router.post("/classification/run")
def run_classification(session: Session = Depends(get_session)) -> dict[str, int]:
    bank_returns_linked = link_bank_return_transactions(session)
    classified = classify_all_deals(session)
    return {"classified": classified, "bank_returns_linked": bank_returns_linked}


@router.post("/receipts/reconcile")
def run_receipt_reconciliation(session: Session = Depends(get_session)) -> dict[str, int]:
    linked = reconcile_receipts(session)
    return {"linked": linked}


@router.get("/deals", response_model=list[DealOut])
def list_deals(session: Session = Depends(get_session)) -> list[Deal]:
    return session.scalars(select(Deal).order_by(Deal.created_at.desc())).all()


@router.get("/reports/reconciliation", response_model=ReconciliationSummary)
def reconciliation_summary(session: Session = Depends(get_session)) -> ReconciliationSummary:
    return build_reconciliation_summary(session)


@router.get("/reports/dashboard", response_model=TelegramDashboardSnapshot)
def dashboard_snapshot(
    session: Session = Depends(get_session),
    recent_deals_limit: int = Query(default=10, ge=1, le=50),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> TelegramDashboardSnapshot:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be less than or equal to date_to")
    try:
        auto_sync_recent_sber(session, reason="dashboard")
    except Exception as exc:
        # Reporting should stay available even if Sber is temporarily unavailable.
        print(f"[auto-sync] dashboard refresh failed: {exc}")
    return build_dashboard_snapshot(
        session,
        recent_deals_limit=recent_deals_limit,
        date_from=date_from,
        date_to=date_to,
    )
