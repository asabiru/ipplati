from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramWalletReaderSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000"
    telegram_exe_path: str = ""
    telegram_window_title_contains: str = "Telegram"
    artifacts_dir: str = "artifacts/telegram-wallet"
    downloads_dir: str = r"C:\Users\stasb\Downloads"
    csv_pattern: str = "p2p-order-history_*.csv"
    wait_timeout_seconds: int = 600
    poll_interval_seconds: int = 2

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_WALLET_READER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


telegram_wallet_reader_settings = TelegramWalletReaderSettings()
