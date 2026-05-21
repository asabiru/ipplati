from pydantic_settings import BaseSettings, SettingsConfigDict


class ReaderSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000"
    artifacts_dir: str = "artifacts"
    reader_session_prefix: str = "wallet-reader"
    request_timeout_seconds: int = 30
    poll_interval_seconds: int = 15

    model_config = SettingsConfigDict(
        env_prefix="READER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


reader_settings = ReaderSettings()
