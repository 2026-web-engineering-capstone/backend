from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database
from app.enums import Role
from app.models import User
from app.service import AppService


settings = Settings()
database = Database(settings.database_url)
service = AppService(database.session_factory)


def configure_runtime(new_settings: Settings | None = None) -> None:
    global settings, database, service
    settings = new_settings or Settings()
    database = Database(settings.database_url)
    service = AppService(database.session_factory)


def get_settings() -> Settings:
    return settings


def get_db() -> Generator[Session, None, None]:
    yield from database.session()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = db.get(User, session_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalid")

    return user


def require_roles(*roles: Role):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency


def get_service() -> AppService:
    return service
