from pydantic_settings import BaseSettings, SettingsConfigDict


class WalletWebReaderSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000"
    headless: bool = False
    slow_mo_ms: int = 150
    browser_channel: str = "chrome"
    artifacts_dir: str = "artifacts/wallet"
    storage_state_path: str = "artifacts/wallet/storage-state.json"
    base_url: str = "https://wallet.tg"
    orders_url: str = "https://wallet.tg/p2p"
    downloads_dir: str = "artifacts/wallet/downloads"
    screenshot_on_error: bool = True
    request_timeout_seconds: int = 30
    login_wait_url_pattern: str = "**/p2p**"
    orders_ready_selector: str = "body"
    history_nav_selectors: str = (
        'text="Orders"||text="History"||text="P2P"||a[href*="/p2p"]||button:has-text("P2P")'
    )
    export_button_selectors: str = (
        'text="Export"||button:has-text("Export")||a:has-text("Export")||text="Download CSV"'
    )
    export_csv_selectors: str = (
        'text=".csv"||text="CSV"||button:has-text("CSV")||a:has-text("CSV")'
    )
    download_filename_pattern: str = "p2p-order-history_*.csv"

    model_config = SettingsConfigDict(
        env_prefix="WALLET_WEB_READER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


wallet_web_reader_settings = WalletWebReaderSettings()
