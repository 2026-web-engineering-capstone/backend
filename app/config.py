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
    # 키가 비어 있거나 호출이 실패하면 transit_service가 fallback 데모 데이터를 반환한다.
    seoul_open_api_key: str | None = None
    subway_arrival_api_key: str | None = None
    seoul_open_api_base_url: str = "http://swopenapi.seoul.go.kr/api/subway"
    # 실시간 도착 정보 캐시 TTL(초).
    transit_arrivals_cache_ttl: int = 20

    # Firebase Cloud Messaging 서비스 계정 JSON 파일 경로.
    # 미설정 시 푸시 발송은 no-op으로 폴백하고 WebSocket으로 실시간 업데이트만 동작.
    firebase_credentials_path: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="GYOUM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
