from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.dependencies import (
    create_user_session,
    delete_user_session,
    get_current_user,
    get_db,
    get_service,
    get_settings,
    get_updates_hub,
)
from app.realtime import SupportRequestUpdatesHub
from app.models import User
from app.schemas import ApiResponse, SessionResponse, SessionUser, SignInRequest
from app.service import AppService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sign-in", response_model=ApiResponse)
def sign_in(
    payload: SignInRequest,
    response: Response,
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    user = service.get_demo_user_for_role(db, payload.role)
    user_session = create_user_session(db, user)
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=user_session.id,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return ApiResponse(data=SessionResponse(user=_to_session_user(user)))


@router.post("/sign-out", response_model=ApiResponse)
async def sign_out(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    delete_user_session(db, session_id)
    if session_id:
        await updates_hub.disconnect_session(session_id)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )
    return ApiResponse(data={"signed_out": True})


@router.get("/session", response_model=ApiResponse)
def get_session(user: User = Depends(get_current_user)):
    return ApiResponse(data=SessionResponse(user=_to_session_user(user)))


def _to_session_user(user: User) -> SessionUser:
    return SessionUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        station_id=user.station_id,
    )
