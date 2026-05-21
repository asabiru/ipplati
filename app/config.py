from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "IP Automation"
    app_env: str = "dev"
    database_url: str = "sqlite:///./automation.db"
    match_window_hours: int = 24
    match_amount_tolerance: float = 0.0
    public_base_url: str = "http://127.0.0.1:8000"
    sber_redirect_uri: str = "http://127.0.0.1:8000/sber/callback"
    sber_client_id: str = ""
    sber_client_secret: str = ""
    sber_scope: str = "openid GET_STATEMENT_ACCOUNT"
    sber_oauth_authorize_url: str = "https://sbi.sberbank.ru:9443/ic/sso/api/v2/oauth/authorize"
    sber_oauth_token_url: str = "https://fintech.sberbank.ru:9443/ic/sso/api/v2/oauth/token"
    sber_oauth_userinfo_url: str = "https://fintech.sberbank.ru:9443/ic/sso/api/v2/oauth/user-info"
    sber_statement_transactions_url: str = "https://fintech.sberbank.ru:9443/fintech/api/v2/statement/transactions"
    sber_default_account_number: str = ""
    sber_state_secret: str = "change-me"
    sber_verify_tls: bool = False
    sber_tls_p12_path: str = ""
    sber_tls_p12_password: str = ""
    sber_tls_cert_path: str = ""
    sber_tls_key_path: str = ""
    sber_tls_ca_chain_path: str = ""
    sber_auto_sync_enabled: bool = True
    sber_auto_sync_lookback_days: int = 3
    sber_auto_sync_min_interval_seconds: int = 180
    sber_auto_sync_poll_interval_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
