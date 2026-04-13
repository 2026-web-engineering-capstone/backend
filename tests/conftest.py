from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import configure_runtime
from app.main import create_app


@pytest.fixture
def app_settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        allowed_origins=["http://localhost:8081"],
        allowed_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    )


@pytest.fixture
def client(app_settings: Settings) -> Generator[TestClient, None, None]:
    configure_runtime(app_settings)
    app = create_app(app_settings)
    with TestClient(app) as test_client:
        yield test_client
