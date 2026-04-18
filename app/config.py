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
    allowed_origin_regex: str = (
        r"https?://("
        r"localhost|127\.0\.0\.1|"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
        r")(?:\:\d+)?$"
    )
    session_cookie_name: str = "gyoum_session"
    cookie_secure: bool = False

    model_config = SettingsConfigDict(
        env_prefix="GYOUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
