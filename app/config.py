from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Mercado Livre App (global defaults, overridden by per-seller credentials)
    ml_app_id: str = ""
    ml_secret_key: str = ""
    ml_redirect_uri: str = ""

    # Conta Azul
    ca_access_token: str = ""
    ca_refresh_token: str = ""
    ca_client_id: str = "4dnledla42eblgbhnsp53lncrg"
    ca_client_secret: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""

    # Server
    base_url: str = "http://localhost:8000"

    # Dashboard CORS origins (comma-separated)
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Faturamento sync interval in minutes
    sync_interval_minutes: int = 5

    # Daily sync handling for non-order payments:
    # - classifier: current V3 classifier flow
    # - legacy: defer non-order handling to legacy report bridge/export
    daily_sync_non_order_mode: str = "classifier"

    # Restrict background jobs to specific sellers (comma-separated slugs).
    # Empty means all active sellers.
    seller_allowlist: str = ""

    # Feature flags
    expenses_api_enabled: bool = True
    nightly_pipeline_enabled: bool = False
    nightly_pipeline_hour_brt: int = 0
    nightly_pipeline_minute_brt: int = 1
    nightly_pipeline_legacy_weekdays: str = "0,3"  # Monday=0, Thursday=3

    # Legacy daily export automation (MP settlement -> legacy ZIP -> optional upload)
    legacy_daily_enabled: bool = False
    legacy_daily_hour_brt: int = 6
    legacy_daily_minute_brt: int = 15
    legacy_daily_upload_mode: str = "http"  # http | gdrive
    legacy_daily_upload_url: str = ""
    legacy_daily_upload_token: str = ""
    legacy_daily_upload_timeout_seconds: int = 120
    legacy_daily_report_wait_seconds: int = 300
    legacy_daily_default_centro_custo: str = "NETAIR"
    legacy_daily_google_drive_root_folder_id: str = ""
    legacy_daily_google_drive_id: str = ""  # Shared Drive ID (optional)
    legacy_daily_google_service_account_json: str = ""
    legacy_daily_google_service_account_file: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
