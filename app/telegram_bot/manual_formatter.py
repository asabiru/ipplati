from __future__ import annotations

from decimal import Decimal


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _format_rate(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}".replace(",", " ")


def format_manual_deal_intro() -> str:
    return "\n".join(
        [
            "<b>Ручное добавление платежа</b>",
            "",
            "Выберите конкретный непривязанный платеж из Сбера.",
            "Потом бот спросит сторону сделки и курс, а сумму в RUB возьмет из платежа автоматически.",
        ]
    )


def format_manual_bank_transactions(items: list[object]) -> str:
    if not items:
        return "\n".join(
            [
                "<b>Ручное добавление платежа</b>",
                "",
                "Сейчас нет непривязанных платежей из Сбера.",
            ]
        )

    lines = ["<b>Выберите платеж из Сбера</b>", ""]
    for item in items:
        booked_at = getattr(item, "booked_at", None)
        amount = getattr(item, "amount", Decimal("0"))
        direction = getattr(item, "direction", None)
        payer_name = getattr(item, "payer_name", None) or "-"
        external_id = getattr(item, "external_id", "")
        direction_value = getattr(direction, "value", str(direction))
        if booked_at is not None:
            lines.append(
                f"{booked_at:%Y-%m-%d %H:%M} | {direction_value} | {_money(amount)} RUB | {payer_name} | {external_id}"
            )
        else:
            lines.append(f"{direction_value} | {_money(amount)} RUB | {payer_name} | {external_id}")
    return "\n".join(lines)


def format_manual_side_prompt() -> str:
    return "\n".join(
        [
            "<b>Выберите сторону сделки</b>",
            "",
            "Покупка = вы купили USDT.",
            "Продажа = вы продали USDT.",
        ]
    )


def format_manual_rate_prompt() -> str:
    return "\n".join(
        [
            "<b>Введите курс и комментарий</b>",
            "",
            "Формат сообщения:",
            "<code>81.55 Комментарий к платежу</code>",
            "",
            "Комментарий необязателен. Объем USDT бот рассчитает сам из суммы платежа и курса.",
        ]
    )


def format_manual_deal_result(result: object) -> str:
    deal = getattr(result, "deal")
    raw_payload = deal.raw_payload or {}
    side = raw_payload.get("side", "-")
    rate_raw = raw_payload.get("price")
    crypto_amount = raw_payload.get("net_crypto_amount")
    try:
        rate = Decimal(str(rate_raw)) if rate_raw is not None else None
    except Exception:
        rate = None
    comment = raw_payload.get("manual_comment") or deal.notes or "-"
    return "\n".join(
        [
            "<b>Ручной платеж добавлен</b>",
            "",
            f"Сделка: <b>{deal.external_id}</b>",
            f"Сторона: <b>{side}</b>",
            f"Сумма: <b>{_money(deal.expected_fiat_amount)}</b> {deal.fiat_currency}",
            f"Курс: <b>{_format_rate(rate)}</b>",
            f"Объем: <b>{crypto_amount or '-'}</b> {raw_payload.get('crypto_currency') or 'USDT'}",
            f"Комментарий: <b>{comment}</b>",
        ]
    )


def format_manual_rate_invalid() -> str:
    return "\n".join(
        [
            "Не удалось распознать курс.",
            "Отправьте сообщение так: <code>81.55 Комментарий</code>",
        ]
    )
