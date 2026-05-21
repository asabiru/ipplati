from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DealStatus(StrEnum):
    detected = "detected"
    awaiting_payment = "awaiting_payment"
    matched = "matched"
    receipt_pending = "receipt_pending"
    receipted = "receipted"
    third_party_review = "third_party_review"
    return_detected = "return_detected"
    canceled = "canceled"


class ReceiptType(StrEnum):
    sale = "sale"
    refund = "refund"


class ReceiptStatus(StrEnum):
    issued = "issued"
    failed = "failed"
    pending = "pending"


class TransactionDirection(StrEnum):
    incoming = "incoming"
    outgoing = "outgoing"


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default="wallet")
    status: Mapped[DealStatus] = mapped_column(Enum(DealStatus), default=DealStatus.detected, index=True)
    fiat_currency: Mapped[str] = mapped_column(String(8), default="RUB")
    expected_fiat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    counterparty_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    counterparty_wallet_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    return_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    bank_transactions: Mapped[list[BankTransaction]] = relationship(back_populates="deal")
    receipts: Mapped[list[FiscalReceipt]] = relationship(back_populates="deal")
    match_decisions: Mapped[list[MatchDecision]] = relationship(back_populates="deal")


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="sber")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), index=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    direction: Mapped[TransactionDirection] = mapped_column(
        Enum(TransactionDirection),
        default=TransactionDirection.incoming,
        index=True,
    )
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payer_account_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deal_id: Mapped[str | None] = mapped_column(ForeignKey("deals.id"), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    deal: Mapped[Deal | None] = relationship(back_populates="bank_transactions")
    match_decisions: Mapped[list[MatchDecision]] = relationship(back_populates="bank_transaction")


class FiscalReceipt(Base):
    __tablename__ = "fiscal_receipts"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_receipt_provider_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="astral")
    receipt_type: Mapped[ReceiptType] = mapped_column(Enum(ReceiptType))
    status: Mapped[ReceiptStatus] = mapped_column(Enum(ReceiptStatus), default=ReceiptStatus.issued)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deal_id: Mapped[str | None] = mapped_column(ForeignKey("deals.id"), nullable=True, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    deal: Mapped[Deal | None] = relationship(back_populates="receipts")


class MatchDecision(Base):
    __tablename__ = "match_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    deal_id: Mapped[str] = mapped_column(ForeignKey("deals.id"), index=True)
    bank_transaction_id: Mapped[str] = mapped_column(ForeignKey("bank_transactions.id"), index=True)
    score: Mapped[int] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(32))
    rule_hits: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    deal: Mapped[Deal] = relationship(back_populates="match_decisions")
    bank_transaction: Mapped[BankTransaction] = relationship(back_populates="match_decisions")


class SberToken(Base):
    __tablename__ = "sber_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    id_token: Mapped[str | None] = mapped_column(String, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_in_minutes: Mapped[int | None] = mapped_column(nullable=True)
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
