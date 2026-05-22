from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.stations import router as stations_router
from app.api.support_requests import router as support_requests_router
from app.api.transit import router as transit_router
from app.config import Settings
from app import dependencies


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is not None:
        dependencies.configure_runtime(settings)

    current_settings = dependencies.get_settings()
    app = FastAPI(title=current_settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=current_settings.allowed_origins,
        allow_origin_regex=current_settings.allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(stations_router)
    app.include_router(support_requests_router)
    app.include_router(transit_router)

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

    @app.on_event("startup")
    def on_startup() -> None:
        dependencies.database.create_all()
        dependencies.service.seed()

    return app


app = create_app()
