from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = BASE_DIR / "gyoum.db"


class Settings(BaseSettings):
    app_name: str = "Gyoum API"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH}"
    allowed_origins: list[str] = [
        "http://localhost:8081",
        "http://localhost:19006",
        "http://localhost:3000",
    ]
    allowed_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    session_cookie_name: str = "gyoum_session"
    cookie_secure: bool = False

    model_config = SettingsConfigDict(
        env_prefix="GYOUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
