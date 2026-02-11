from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Mercado Livre App
    ml_app_id: str = ""
    ml_secret_key: str = ""
    ml_redirect_uri: str = ""

    # Conta Azul
    ca_access_token: str = ""
    ca_refresh_token: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""

    # Server
    base_url: str = "http://localhost:8000"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
