from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OfdReaderSettings(BaseSettings):
    api_base_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: int = 30
    headless: bool = False
    slow_mo_ms: int = 150
    browser_channel: str = "chrome"
    storage_state_path: str = "artifacts/ofd/storage-state.json"
    artifacts_dir: str = "artifacts/ofd"
    base_url: str = "https://ofd.astralnalog.ru"
    dashboard_url: str = "https://ofd.astralnalog.ru/lk/dashboard"
    checks_url: str = "https://ofd.astralnalog.ru/lk/receipts"
    login_url: str = "https://identity.astral.ru/Account/classic"
    username: str = ""
    password: str = ""
    cookie_accept_selector: str = "text=Хорошо"
    login_entry_selector: str = "text=Войти в ЛК"
    existing_account_selector: str = "text=Войти"
    username_selector: str = 'input[type="text"], input[name="login"], input[name="username"]'
    phone_selector: str = 'input[type="tel"], input[name="phone"]'
    password_selector: str = 'input[type="password"]'
    submit_selector: str = 'button[type="submit"]'
    checks_nav_selector: str = "text=Чеки"
    table_selector: str = "table"
    next_page_selector: str = 'button[aria-label*="next"], button:has-text("След"), a:has-text("След")'
    max_pages: int = 5
    screenshot_on_error: bool = True
    organization_id: str = "472076"
    api_page_size: int = 100
    receipt_type_sale_keywords: list[str] = Field(default_factory=lambda: ["приход", "sale"])
    receipt_type_refund_keywords: list[str] = Field(default_factory=lambda: ["возврат", "refund"])

    model_config = SettingsConfigDict(
        env_prefix="OFD_READER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


ofd_reader_settings = OfdReaderSettings()
