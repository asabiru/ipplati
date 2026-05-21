from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas import DealOut, TelegramDashboardSnapshot, WalletTradingStats


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _decimal_from_raw(value) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).replace("\u00a0", " ").replace(" ", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _wallet_side_label(raw_payload: dict) -> str:
    explicit_side = str(raw_payload.get("side") or "").strip().lower()
    if explicit_side in {"buy", "sell"}:
        return explicit_side

    ad_type = str(raw_payload.get("ad_type") or "").strip().lower()
    role = str(raw_payload.get("role") or "").strip().lower()
    combined = f"{ad_type} {role}"
    if any(token in combined for token in ("buy", "purchase", "buyer")):
        return "buy"
    if any(token in combined for token in ("sell", "sale", "seller")):
        return "sell"
    return "n/a"


def _format_rate(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}".replace(",", " ")


def _format_wallet_trading_stats(stats: WalletTradingStats) -> list[str]:
    currency = stats.crypto_currency or "crypto"
    lines = [
        "<b>Торговля</b>",
        f"Покупок сопоставлено: <b>{stats.matched_buy_deals}</b>",
        f"Продаж сопоставлено: <b>{stats.matched_sell_deals}</b>",
        f"Оборот buy: <b>{_money(stats.buy_turnover_rub)}</b> RUB",
        f"Оборот sell: <b>{_money(stats.sell_turnover_rub)}</b> RUB",
        f"Общий оборот: <b>{_money(stats.total_turnover_rub)}</b> RUB",
        f"Средний buy курс: <b>{_format_rate(stats.avg_buy_net_rate)}</b>",
        f"Средний sell курс: <b>{_format_rate(stats.avg_sell_net_rate)}</b>",
        f"Реализованная прибыль: <b>{_money(stats.realized_profit_rub)}</b> RUB",
        f"Реализовано: <b>{stats.realized_sell_crypto_amount}</b> {currency}",
        f"Баланс USDT: <b>{stats.balance_crypto_amount}</b> {currency}",
    ]
    if stats.balance_crypto_amount < 0:
        lines.append("<b>Нужно докупить USDT, потому что продано больше, чем куплено.</b>")
    return lines


def format_summary(snapshot: TelegramDashboardSnapshot) -> str:
    summary = snapshot.summary
    if snapshot.date_from and snapshot.date_to:
        period = f"{snapshot.date_from.isoformat()} - {snapshot.date_to.isoformat()}"
    else:
        period = "все время"

    lines = [
        "<b>Статистика</b>",
        "",
        f"Период: <b>{period}</b>",
        f"Сделок Wallet: <b>{summary.total_deals}</b>",
        f"Сопоставлено с банком: <b>{summary.matched_deals}</b>",
        f"Возвратных: <b>{summary.return_detected_deals}</b>",
        f"Возвратов, RUB: <b>{_money(summary.total_returned_rub)}</b>",
        f"Не удалось сопоставить: <b>{snapshot.unmatched_wallet_deals_count}</b>",
    ]
    if snapshot.unmatched_wallet_deal_ids:
        lines.extend(
            [
                "Непривязанные сделки:",
                ", ".join(snapshot.unmatched_wallet_deal_ids),
            ]
        )

    lines.append("")
    lines.extend(_format_wallet_trading_stats(snapshot.wallet_trading_stats))
    lines.extend(
        [
            "",
            f"Чеков продаж: <b>{snapshot.sale_receipts}</b>",
            f"Чеков возврата: <b>{snapshot.refund_receipts}</b>",
            "",
            "<i>Заработок и оборот считаются только по сопоставленным сделкам Wallet, исключая возвраты. Комиссия Wallet учитывается один раз через Net Crypto Amount.</i>",
            "",
            "<b>Wallet CSV</b>",
            snapshot.wallet_csv_message,
        ]
    )
    if not snapshot.wallet_csv_has_coverage:
        lines.append("Пришлите сюда CSV-файл из TG Wallet за нужный период.")
    return "\n".join(lines)


def format_recent_deals(deals: list[DealOut]) -> str:
    lines = ["<b>Последние сделки</b>", ""]
    if not deals:
        lines.append("Сделок пока нет.")
        return "\n".join(lines)

    for deal in deals:
        raw_payload = deal.raw_payload or {}
        net_crypto = _decimal_from_raw(raw_payload.get("net_crypto_amount"))
        fee_crypto = _decimal_from_raw(raw_payload.get("paid_fee_crypto_amount")) or Decimal("0")
        listed_rate = _decimal_from_raw(raw_payload.get("price"))
        crypto_currency = raw_payload.get("crypto_currency") or "crypto"
        side = _wallet_side_label(raw_payload)
        calculated_rate = listed_rate
        if calculated_rate is None and net_crypto and net_crypto > 0:
            calculated_rate = deal.expected_fiat_amount / net_crypto

        lines.append(
            f"{deal.external_id} | {side} | {deal.status.value} | {deal.expected_fiat_amount} {deal.fiat_currency}"
        )
        lines.append(
            f"курс: {_format_rate(listed_rate)} | расчетный курс: {_format_rate(calculated_rate)} | net: {net_crypto or '-'} {crypto_currency} | fee: {fee_crypto} {crypto_currency}"
        )
    return "\n".join(lines)


def format_returns(snapshot: TelegramDashboardSnapshot) -> str:
    if snapshot.date_from and snapshot.date_to:
        period = f"{snapshot.date_from.isoformat()} - {snapshot.date_to.isoformat()}"
    else:
        period = "все время"

    lines = [
        "<b>Возвраты</b>",
        "",
        f"Период: <b>{period}</b>",
        f"Возвратных сделок: <b>{snapshot.summary.return_detected_deals}</b>",
        f"Сумма возвратов, RUB: <b>{_money(snapshot.summary.total_returned_rub)}</b>",
        f"Чеков возврата: <b>{snapshot.refund_receipts}</b>",
        f"Текущая прибыль без возвратов: <b>{_money(snapshot.wallet_trading_stats.realized_profit_rub)}</b> RUB",
        "",
        f"Wallet CSV: {snapshot.wallet_csv_message}",
    ]
    if not snapshot.wallet_csv_has_coverage:
        lines.append("Пришлите сюда CSV-файл из TG Wallet за нужный период.")
    return "\n".join(lines)


def format_help() -> str:
    return "\n".join(
        [
            "<b>Команды бота</b>",
            "",
            "/report - общий отчет",
            "/deals - последние сделки",
            "/returns - возвраты",
            "/help - список команд",
        ]
    )


def format_custom_date_prompt() -> str:
    return "\n".join(
        [
            "<b>Фильтр по датам</b>",
            "",
            "Отправьте диапазон в одном сообщении.",
            "Формат: <code>2026-05-01 2026-05-31</code>",
            "Или: <code>2026-05-01..2026-05-31</code>",
        ]
    )


def format_invalid_date_prompt() -> str:
    return "\n".join(
        [
            "Не удалось распознать даты.",
            "Отправьте диапазон так: <code>2026-05-01 2026-05-31</code>",
        ]
    )


def format_wallet_csv_import_result(
    *,
    filename: str,
    processed_rows: int,
    report: dict,
) -> str:
    lines = [
        "<b>CSV из TG Wallet загружен</b>",
        "",
        f"Файл: <b>{filename}</b>",
        f"Обработано ордеров: <b>{processed_rows}</b>",
        "Учет идет по <b>Order Number</b>, поэтому существующие ордера не дублируются.",
        "",
        f"Сделок всего: <b>{report.get('total_deals', 0)}</b>",
        f"Сопоставлено с банком: <b>{report.get('matched_deals', 0)}</b>",
        f"Возвратных: <b>{report.get('return_detected_deals', 0)}</b>",
    ]
    return "\n".join(lines)


def format_period_label(date_from: date | None, date_to: date | None) -> str:
    if date_from and date_to:
        return f"{date_from.isoformat()} - {date_to.isoformat()}"
    return "все время"
