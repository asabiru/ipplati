from __future__ import annotations

import argparse

from app.telegram_bot.bot import run_polling
from app.telegram_bot.client import TelegramBotApi
from app.telegram_bot.config import telegram_bot_settings
from app.telegram_bot.dashboard_client import DashboardApiClient
from app.telegram_bot.formatter import format_help, format_recent_deals, format_returns, format_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram bot for project statistics")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("poll", help="Run the Telegram bot in long polling mode")

    test_parser = subparsers.add_parser("send-report", help="Send the current report to a specific chat")
    test_parser.add_argument("--chat-id", required=True, type=int, help="Telegram chat id")
    test_parser.add_argument(
        "--kind",
        choices=["report", "deals", "returns", "help"],
        default="report",
        help="Which message to send",
    )
    return parser


def run_send_report(chat_id: int, kind: str) -> int:
    telegram = TelegramBotApi()
    dashboard = DashboardApiClient()

    if kind == "report":
        text = format_summary(dashboard.get_dashboard())
    elif kind == "deals":
        text = format_recent_deals(dashboard.get_recent_deals())
    elif kind == "returns":
        text = format_returns(dashboard.get_dashboard())
    else:
        text = format_help()

    telegram.send_message(chat_id, text)
    print(f"sent {kind} to {chat_id}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "poll":
        if not telegram_bot_settings.bot_token:
            raise RuntimeError("TELEGRAM_BOT_BOT_TOKEN is not configured")
        run_polling()
        return 0
    if args.command == "send-report":
        return run_send_report(args.chat_id, args.kind)
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
