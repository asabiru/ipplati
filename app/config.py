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

    # Alfa-Bank
    alfa_client_id: str = ""
    alfa_client_secret: str = ""
    alfa_api_key: str = ""
    alfa_scope: str = "transactions"
    alfa_oauth_authorize_url: str = "https://baas.alfabank.ru/oidc/authorize"
    alfa_oauth_token_url: str = "https://baas.alfabank.ru/oidc/token"
    alfa_redirect_uri: str = "http://127.0.0.1:8000/alfa/callback"
    alfa_statement_transactions_url: str = "https://baas.alfabank.ru/api/jp/v1/statement/transactions"
    alfa_default_account_number: str = ""
    alfa_state_secret: str = "change-me-alfa"
    alfa_verify_tls: bool = True
    alfa_tls_cert_path: str = ""
    alfa_tls_key_path: str = ""
    alfa_tls_key_password: str = ""
    alfa_tls_ca_chain_path: str = ""
    alfa_auto_sync_enabled: bool = False
    alfa_auto_sync_lookback_days: int = 3
    alfa_auto_sync_min_interval_seconds: int = 180
    alfa_auto_sync_poll_interval_seconds: int = 300

    kudir_ip_name: str = "ИП Поляков Александр Александрович"
    kudir_inn: str = "631944033067"
    kudir_tax_year: int = 2026
    kudir_tax_system: str = "УСН (доходы − расходы)"
    kudir_income_basis: str = "Доход от оказания посреднических услуг"
    kudir_expense_third_party_basis: str = "Расходы на оплату услуг третьих лиц, связанных с осуществлением посреднической деятельности"
    kudir_expense_bank_fee_basis: str = "Банковская комиссия за расчетно-кассовое обслуживание (пакет переводов физическим лицам)"
    kudir_artifacts_dir: str = "artifacts/kudir"
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
