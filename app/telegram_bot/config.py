from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramBotSettings(BaseSettings):
    bot_token: str = ""
    api_base_url: str = "http://127.0.0.1:8000"
    poll_timeout_seconds: int = 30
    recent_deals_limit: int = 5
    uploads_dir: str = "artifacts/telegram-bot/uploads"
    state_file: str = "artifacts/telegram-bot/offset.txt"

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


telegram_bot_settings = TelegramBotSettings()
