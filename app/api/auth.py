from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
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
from app.schemas import (
    ApiResponse,
    RegisterPushTokenRequest,
    SessionResponse,
    SessionUser,
    SignInRequest,
    SignOutRequest,
    UnregisterPushTokenRequest,
)
from app.service import AppService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sign-in", response_model=ApiResponse)
def sign_in(
    payload: SignInRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
):
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id:
        try:
            get_current_user(request, db)
        except HTTPException:
            delete_user_session(db, session_id)
            session_id = None

    user = service.get_demo_user_for_role(db, payload.role, payload.station_id)
    if payload.installation_id and payload.push_token and payload.push_platform:
        service.register_push_token(
            db,
            user,
            payload.push_token,
            payload.push_platform,
            payload.installation_id,
        )
    user_session = create_user_session(db, user)
    delete_user_session(db, session_id)
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
    payload: SignOutRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    updates_hub: SupportRequestUpdatesHub = Depends(get_updates_hub),
):
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    user = None
    if session_id:
        try:
            user = get_current_user(request, db)
        except HTTPException:
            user = None
    if payload is not None:
        if user:
            service.unregister_push_token_for_user(
                db,
                user.id,
                payload.installation_id,
                payload.push_token,
            )
        elif payload.push_token:
            service.unregister_push_token_for_installation(
                db,
                payload.installation_id,
                payload.push_token,
            )
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


@router.post("/push-token", response_model=ApiResponse)
def register_push_token(
    payload: RegisterPushTokenRequest,
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    user: User = Depends(get_current_user),
):
    service.register_push_token(
        db,
        user,
        payload.token,
        payload.platform,
        payload.installation_id,
    )
    return ApiResponse(data={"registered": True})


@router.delete("/push-token", response_model=ApiResponse)
def unregister_push_token(
    payload: UnregisterPushTokenRequest,
    db: Session = Depends(get_db),
    service: AppService = Depends(get_service),
    user: User = Depends(get_current_user),
):
    service.unregister_push_token(db, user, payload.installation_id)
    return ApiResponse(data={"unregistered": True})


def _to_session_user(user: User) -> SessionUser:
    return SessionUser(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        station_id=user.station_id,
        station_name=user.station.name if user.station else None,
    )
