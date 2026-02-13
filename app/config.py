from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Mercado Livre App (global defaults, overridden by per-seller credentials)
    ml_app_id: str = ""
    ml_secret_key: str = ""
    ml_redirect_uri: str = ""

    # Conta Azul
    ca_access_token: str = ""
    ca_refresh_token: str = ""
    ca_client_id: str = "6ri07ptg5k2u7dubdlttg3a7t8"
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
