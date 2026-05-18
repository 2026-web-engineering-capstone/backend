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

    # 외부 API 자격 증명 — 코드 하드코딩 금지. .env 또는 환경 변수로만 주입.
    # 서울 열린데이터 광장: http://swopenapi.seoul.go.kr/api/subway
    seoul_open_api_key: str | None = None
    seoul_open_api_base_url: str = "http://swopenapi.seoul.go.kr/api/subway"
    # 공공데이터포털: 도시철도 역사 편의시설 데이터셋 (사용자가 base URL 확정 필요)
    facility_api_key: str | None = None
    facility_api_base_url: str | None = None
    # 외부 API 캐시 TTL(초). 도착 정보는 짧게, 시설 정보는 길게.
    transit_arrivals_cache_ttl: int = 20
    transit_facilities_cache_ttl: int = 86400

    model_config = SettingsConfigDict(
        env_prefix="GYOUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
