from pydantic_settings import BaseSettings, SettingsConfigDict


class SyncSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000"
    poll_interval_seconds: int = 30
    wallet_directory: str = r"C:\Users\stasb\Downloads"
    wallet_pattern: str = "p2p-order-history_*.csv"
    sber_directory: str = r"C:\Users\stasb\Downloads"
    sber_pattern: str = "sber-*.csv"
    ofd_directory: str = r"C:\Users\stasb\Downloads"
    ofd_pattern: str = "ofd-*.csv"

    model_config = SettingsConfigDict(
        env_prefix="SYNC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


sync_settings = SyncSettings()
