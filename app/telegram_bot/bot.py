from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re
import time

from app.telegram_bot.client import TelegramBotApi, TelegramUpdate
from app.telegram_bot.config import telegram_bot_settings
from app.telegram_bot.dashboard_client import DashboardApiClient
from app.telegram_bot.formatter import (
    format_custom_date_prompt,
    format_help,
    format_invalid_date_prompt,
    format_recent_deals,
    format_returns,
    format_summary,
    format_wallet_csv_import_result,
)
from app.telegram_bot.manual_formatter import (
    format_manual_bank_transactions,
    format_manual_deal_intro,
    format_manual_deal_result,
    format_manual_rate_invalid,
    format_manual_rate_prompt,
    format_manual_side_prompt,
)
from app.telegram_bot.uploads import import_wallet_csv_from_upload, is_wallet_csv_filename, save_telegram_wallet_csv


DATE_RANGE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:\.\.|-|\s)\s*(\d{4}-\d{2}-\d{2})\s*$")
RATE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(.*)$")


@dataclass(slots=True)
class BotContext:
    telegram: TelegramBotApi
    dashboard: DashboardApiClient


@dataclass(slots=True)
class PendingRangeRequest:
    mode: str
    date_from: date | None = None
    date_to: date | None = None


@dataclass(slots=True)
class PendingManualDealRequest:
    bank_transaction_external_id: str
    side: str | None = None


PENDING_RANGE_REQUESTS: dict[int, PendingRangeRequest] = {}
PENDING_MANUAL_REQUESTS: dict[int, PendingManualDealRequest] = {}


def _state_file_path() -> Path:
    path = Path(telegram_bot_settings.state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_offset() -> int | None:
    path = _state_file_path()
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _save_offset(offset: int) -> None:
    path = _state_file_path()
    path.write_text(str(offset), encoding="utf-8")


def _extract_message(update: TelegramUpdate) -> dict | None:
    payload = update.payload
    return payload.get("message") or payload.get("edited_message")


def _extract_callback_query(update: TelegramUpdate) -> dict | None:
    return update.payload.get("callback_query")


def _extract_command(message: dict) -> str:
    text = str(message.get("text") or "").strip()
    return text.split()[0].lower() if text else ""


def _main_menu_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Отчет за месяц", "callback_data": "report:month"},
                {"text": "Отчет за все время", "callback_data": "report:all"},
            ],
            [
                {"text": "Отчет по датам", "callback_data": "report:custom"},
            ],
            [
                {"text": "Возвраты", "callback_data": "returns:month"},
                {"text": "Последние сделки", "callback_data": "deals:month"},
            ],
            [
                {"text": "КУДиР за месяц", "callback_data": "kudir:month"},
                {"text": "КУДиР всё время", "callback_data": "kudir:all"},
                {"text": "КУДиР по датам", "callback_data": "kudir:custom"},
            ],
            [
                {"text": "Ручной платеж", "callback_data": "manual:start"},
            ],
        ]
    }


def _manual_transactions_markup(items: list[object]) -> dict:
    keyboard: list[list[dict[str, str]]] = []
    for item in items:
        booked_at = getattr(item, "booked_at", None)
        amount = getattr(item, "amount", None)
        direction = getattr(getattr(item, "direction", None), "value", str(getattr(item, "direction", "")))
        label_date = booked_at.strftime("%m-%d %H:%M") if booked_at is not None else "дата?"
        label_amount = f"{amount}"
        keyboard.append(
            [
                {
                    "text": f"{label_date} | {direction} | {label_amount}",
                    "callback_data": f"manual:tx:{getattr(item, 'external_id')}",
                }
            ]
        )
    keyboard.append([{"text": "Назад", "callback_data": "menu:home"}])
    return {"inline_keyboard": keyboard}


def _manual_side_markup(bank_transaction_external_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Покупка", "callback_data": f"manual:side:buy:{bank_transaction_external_id}"},
                {"text": "Продажа", "callback_data": f"manual:side:sell:{bank_transaction_external_id}"},
            ],
            [{"text": "Назад", "callback_data": "manual:start"}],
        ]
    }


def _period_month() -> tuple[date, date]:
    today = datetime.now().date()
    return today.replace(day=1), today


def _parse_date_range(text: str) -> tuple[date, date] | None:
    match = DATE_RANGE_RE.match(text.strip())
    if not match:
        return None
    date_from = date.fromisoformat(match.group(1))
    date_to = date.fromisoformat(match.group(2))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def _parse_rate_and_comment(text: str) -> tuple[Decimal, str | None] | None:
    match = RATE_RE.match(text.strip())
    if not match:
        return None
    raw_rate = match.group(1).replace(",", ".")
    try:
        rate = Decimal(raw_rate)
    except Exception:
        return None
    if rate <= 0:
        return None
    comment = match.group(2).strip() or None
    return rate, comment


def _send_menu_message(context: BotContext, chat_id: int, text: str) -> None:
    context.telegram.send_message_with_markup(chat_id, text, _main_menu_markup())


def _send_summary(context: BotContext, chat_id: int, *, date_from: date | None = None, date_to: date | None = None) -> None:
    snapshot = context.dashboard.get_dashboard(date_from=date_from, date_to=date_to)
    if not snapshot.wallet_csv_has_coverage:
        PENDING_RANGE_REQUESTS[chat_id] = PendingRangeRequest(mode="report", date_from=date_from, date_to=date_to)
    _send_menu_message(context, chat_id, format_summary(snapshot))


def _send_returns(context: BotContext, chat_id: int, *, date_from: date | None = None, date_to: date | None = None) -> None:
    snapshot = context.dashboard.get_dashboard(date_from=date_from, date_to=date_to)
    if not snapshot.wallet_csv_has_coverage:
        PENDING_RANGE_REQUESTS[chat_id] = PendingRangeRequest(mode="returns", date_from=date_from, date_to=date_to)
    _send_menu_message(context, chat_id, format_returns(snapshot))


def _send_deals(context: BotContext, chat_id: int, *, date_from: date | None = None, date_to: date | None = None) -> None:
    deals = context.dashboard.get_recent_deals(date_from=date_from, date_to=date_to)
    _send_menu_message(context, chat_id, format_recent_deals(deals))


def _send_manual_start(context: BotContext, chat_id: int) -> None:
    items = context.dashboard.get_unmatched_bank_transactions(limit=10)
    text = format_manual_deal_intro()
    if items:
        text = f"{text}\n\n{format_manual_bank_transactions(items)}"
    context.telegram.send_message_with_markup(chat_id, text, _manual_transactions_markup(items))


def _handle_report(context: BotContext, chat_id: int) -> None:
    date_from, date_to = _period_month()
    _send_summary(context, chat_id, date_from=date_from, date_to=date_to)


def _handle_deals(context: BotContext, chat_id: int) -> None:
    date_from, date_to = _period_month()
    _send_deals(context, chat_id, date_from=date_from, date_to=date_to)


def _handle_returns(context: BotContext, chat_id: int) -> None:
    date_from, date_to = _period_month()
    _send_returns(context, chat_id, date_from=date_from, date_to=date_to)


def _handle_help(context: BotContext, chat_id: int) -> None:
    _send_menu_message(context, chat_id, format_help())


def _handle_kudir(context: BotContext, chat_id: int) -> None:
    _send_kudir(context, chat_id)


def _send_kudir(
    context: BotContext,
    chat_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> None:
    xlsx_bytes, filename = context.dashboard.get_kudir_xlsx(date_from=date_from, date_to=date_to)
    if date_from and date_to:
        period = f"{date_from.isoformat()} — {date_to.isoformat()}"
    else:
        period = "все время"
    caption = f"<b>КУДиР</b>\nПериод: {period}"
    context.telegram.send_document(chat_id, xlsx_bytes, filename, caption=caption)
    _send_menu_message(context, chat_id, "Файл КУДиР отправлен.")


COMMAND_HANDLERS = {
    "/start": _handle_help,
    "/help": _handle_help,
    "/report": _handle_report,
    "/deals": _handle_deals,
    "/returns": _handle_returns,
    "/kudir": _handle_kudir,
}


def _process_pending_range(context: BotContext, chat_id: int, text: str) -> bool:
    pending = PENDING_RANGE_REQUESTS.get(chat_id)
    if pending is None:
        return False

    parsed = _parse_date_range(text)
    if parsed is None:
        _send_menu_message(context, chat_id, format_invalid_date_prompt())
        return True

    date_from, date_to = parsed
    if pending.mode == "report":
        _send_summary(context, chat_id, date_from=date_from, date_to=date_to)
    elif pending.mode == "returns":
        _send_returns(context, chat_id, date_from=date_from, date_to=date_to)
    elif pending.mode == "kudir":
        _send_kudir(context, chat_id, date_from=date_from, date_to=date_to)
    else:
        _send_deals(context, chat_id, date_from=date_from, date_to=date_to)

    PENDING_RANGE_REQUESTS.pop(chat_id, None)
    return True


def _process_pending_manual(context: BotContext, chat_id: int, text: str) -> bool:
    pending = PENDING_MANUAL_REQUESTS.get(chat_id)
    if pending is None:
        return False

    if pending.side is None:
        context.telegram.send_message_with_markup(chat_id, format_manual_side_prompt(), _manual_side_markup(pending.bank_transaction_external_id))
        return True

    parsed = _parse_rate_and_comment(text)
    if parsed is None:
        _send_menu_message(context, chat_id, format_manual_rate_invalid())
        return True

    rate, comment = parsed
    result = context.dashboard.create_manual_deal(
        bank_transaction_external_id=pending.bank_transaction_external_id,
        side=pending.side,
        rate=str(rate),
        comment=comment,
    )
    PENDING_MANUAL_REQUESTS.pop(chat_id, None)
    _send_menu_message(context, chat_id, format_manual_deal_result(result))
    return True


def _handle_callback(context: BotContext, callback_query: dict) -> None:
    callback_id = str(callback_query.get("id"))
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id"))
    data = str(callback_query.get("data") or "")

    if data == "report:month":
        date_from, date_to = _period_month()
        _send_summary(context, chat_id, date_from=date_from, date_to=date_to)
    elif data == "report:all":
        _send_summary(context, chat_id)
    elif data == "report:custom":
        PENDING_RANGE_REQUESTS[chat_id] = PendingRangeRequest(mode="report")
        _send_menu_message(context, chat_id, format_custom_date_prompt())
    elif data == "returns:month":
        date_from, date_to = _period_month()
        _send_returns(context, chat_id, date_from=date_from, date_to=date_to)
    elif data == "deals:month":
        date_from, date_to = _period_month()
        _send_deals(context, chat_id, date_from=date_from, date_to=date_to)
    elif data == "kudir:month":
        date_from, date_to = _period_month()
        _send_kudir(context, chat_id, date_from=date_from, date_to=date_to)
    elif data == "kudir:all":
        _send_kudir(context, chat_id)
    elif data == "kudir:custom":
        PENDING_RANGE_REQUESTS[chat_id] = PendingRangeRequest(mode="kudir")
        _send_menu_message(context, chat_id, format_custom_date_prompt())
    elif data == "manual:start":
        PENDING_MANUAL_REQUESTS.pop(chat_id, None)
        _send_manual_start(context, chat_id)
    elif data.startswith("manual:tx:"):
        bank_transaction_external_id = data.split(":", 2)[2]
        PENDING_MANUAL_REQUESTS[chat_id] = PendingManualDealRequest(bank_transaction_external_id=bank_transaction_external_id)
        context.telegram.send_message_with_markup(
            chat_id,
            format_manual_side_prompt(),
            _manual_side_markup(bank_transaction_external_id),
        )
    elif data.startswith("manual:side:"):
        _, _, side, bank_transaction_external_id = data.split(":", 3)
        PENDING_MANUAL_REQUESTS[chat_id] = PendingManualDealRequest(
            bank_transaction_external_id=bank_transaction_external_id,
            side=side,
        )
        _send_menu_message(context, chat_id, format_manual_rate_prompt())
    elif data == "menu:home":
        _handle_help(context, chat_id)
    else:
        _handle_help(context, chat_id)

    context.telegram.answer_callback_query(callback_id)


def _process_document(context: BotContext, chat_id: int, message: dict) -> bool:
    document = message.get("document")
    if not isinstance(document, dict):
        return False

    filename = document.get("file_name")
    file_id = document.get("file_id")
    if not file_id or not is_wallet_csv_filename(filename):
        _send_menu_message(
            context,
            chat_id,
            "Пришлите CSV из TG Wallet с именем вида <code>p2p-order-history_YYYY-MM-DD_YYYY-MM-DD.csv</code>.",
        )
        return True

    saved_path = save_telegram_wallet_csv(context.telegram, file_id=str(file_id), filename=str(filename))
    result = import_wallet_csv_from_upload(saved_path)
    _send_menu_message(
        context,
        chat_id,
        format_wallet_csv_import_result(
            filename=saved_path.name,
            processed_rows=result.processed_rows,
            report=result.report,
        ),
    )

    pending = PENDING_RANGE_REQUESTS.pop(chat_id, None)
    if pending:
        if pending.mode == "report":
            _send_summary(context, chat_id, date_from=pending.date_from, date_to=pending.date_to)
        elif pending.mode == "returns":
            _send_returns(context, chat_id, date_from=pending.date_from, date_to=pending.date_to)
        else:
            _send_deals(context, chat_id, date_from=pending.date_from, date_to=pending.date_to)
    return True


def process_update(context: BotContext, update: TelegramUpdate) -> None:
    callback_query = _extract_callback_query(update)
    if callback_query is not None:
        _handle_callback(context, callback_query)
        return

    message = _extract_message(update)
    if not message:
        return

    chat = message.get("chat") or {}
    chat_id = int(chat.get("id"))
    if _process_document(context, chat_id, message):
        return

    text = str(message.get("text") or "").strip()

    if text and _process_pending_manual(context, chat_id, text):
        return
    if text and _process_pending_range(context, chat_id, text):
        return

    command = _extract_command(message)
    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        _send_menu_message(context, chat_id, format_help())
        return
    handler(context, chat_id)


def run_polling() -> None:
    context = BotContext(
        telegram=TelegramBotApi(),
        dashboard=DashboardApiClient(),
    )
    offset: int | None = _load_offset()

    while True:
        try:
            updates = context.telegram.get_updates(offset=offset)
        except Exception as exc:
            print(f"telegram polling error: {exc}")
            time.sleep(3)
            continue

        for update in updates:
            offset = update.update_id + 1
            _save_offset(offset)
            try:
                process_update(context, update)
            except Exception as exc:
                print(f"telegram update {update.update_id} failed: {exc}")
