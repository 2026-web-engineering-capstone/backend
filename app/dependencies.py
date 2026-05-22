from collections.abc import Generator
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Database
from app.enums import Role
from app.models import User, UserSession
from app.notifications import FirebaseNotifier, get_notifier, reset_notifier_for_tests
from app.realtime import SupportRequestUpdatesHub
from app.service import AppService


settings = Settings()
database = Database(settings.database_url)
service = AppService(database.session_factory)
updates_hub = SupportRequestUpdatesHub()


def configure_runtime(new_settings: Settings | None = None) -> None:
    global settings, database, service, updates_hub
    settings = new_settings or Settings()
    database = Database(settings.database_url)
    service = AppService(database.session_factory)
    updates_hub = SupportRequestUpdatesHub()
    reset_notifier_for_tests()


def get_firebase_notifier() -> FirebaseNotifier:
    return get_notifier(settings)


def get_settings() -> Settings:
    return settings


def get_db() -> Generator[Session, None, None]:
    yield from database.session()


def create_user_session(db: Session, user: User) -> UserSession:
    session = UserSession(user_id=user.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def delete_user_session(db: Session, session_id: str | None) -> None:
    if not session_id:
        return

    db.execute(delete(UserSession).where(UserSession.id == session_id))
    db.commit()



def _get_user_from_session_id(db: Session, session_id: str | None) -> User:
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_session = db.scalar(
        select(UserSession).where(UserSession.id == session_id)
    )
    if not user_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalid")

    expires_at = user_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= datetime.now(timezone.utc):
        delete_user_session(db, user_session.id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = db.get(User, user_session.user_id)
    if not user:
        delete_user_session(db, user_session.id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalid")

    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    return _get_user_from_session_id(db, request.cookies.get(settings.session_cookie_name))


def get_current_websocket_session(
    websocket: WebSocket,
    db: Session = Depends(get_db),
) -> tuple[User, str]:
    session_id = websocket.cookies.get(settings.session_cookie_name)
    try:
        return _get_user_from_session_id(db, session_id), session_id or ""
    except HTTPException as exc:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail) from exc


def require_roles(*roles: Role):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency


def get_service() -> AppService:
    return service


def get_updates_hub() -> SupportRequestUpdatesHub:
    return updates_hub
