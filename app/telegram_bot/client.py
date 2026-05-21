from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from app.telegram_bot.config import telegram_bot_settings


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    payload: dict


class TelegramBotApi:
    def __init__(self, token: str | None = None, timeout_seconds: int | None = None) -> None:
        self.token = token or telegram_bot_settings.bot_token
        if not self.token:
            raise ValueError("TELEGRAM_BOT_BOT_TOKEN is not configured")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.timeout_seconds = timeout_seconds or telegram_bot_settings.poll_timeout_seconds + 10

    def get_updates(self, offset: int | None = None, timeout_seconds: int | None = None) -> list[TelegramUpdate]:
        params = {"timeout": timeout_seconds or telegram_bot_settings.poll_timeout_seconds}
        if offset is not None:
            params["offset"] = offset

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {payload}")
        return [TelegramUpdate(update_id=item["update_id"], payload=item) for item in payload.get("result", [])]

    def send_message(self, chat_id: int, text: str) -> dict:
        return self.send_message_with_markup(chat_id, text, reply_markup=None)

    def send_message_with_markup(self, chat_id: int, text: str, reply_markup: dict | None) -> dict:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": reply_markup,
                },
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {payload}")
        return payload

    def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> dict:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": reply_markup,
                },
            )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram editMessageText failed: {payload}")
        return payload

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> dict:
        payload_data = {"callback_query_id": callback_query_id}
        if text:
            payload_data["text"] = text
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/answerCallbackQuery", json=payload_data)
        if response.status_code == 400:
            payload = response.json()
            return payload
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram answerCallbackQuery failed: {payload}")
        return payload

    def send_document(
        self,
        chat_id: int,
        document: bytes,
        filename: str,
        caption: str | None = None,
    ) -> dict:
        files = {"document": (filename, document, "application/octet-stream")}
        data: dict[str, str | int] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/sendDocument", data=data, files=files)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram sendDocument failed: {payload}")
        return payload

    def get_file(self, file_id: str) -> dict:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(f"{self.base_url}/getFile", params={"file_id": file_id})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getFile failed: {payload}")
        return payload["result"]

    def download_file(self, file_path: str, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(file_url)
        response.raise_for_status()
        output.write_bytes(response.content)
        return output
