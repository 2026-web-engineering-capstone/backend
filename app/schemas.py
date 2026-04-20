from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    CancelReason,
    MeetingPoint,
    Role,
    SupportRequestStatus,
    SupportType,
    UnavailableReason,
)


class ApiResponse(BaseModel):
    success: bool = True
    data: object | None = None
    error: str | None = None


class SessionUser(BaseModel):
    id: str
    name: str
    email: str
    role: Role
    station_id: str | None = None


class SessionResponse(BaseModel):
    user: SessionUser


class SignInRequest(BaseModel):
    role: Role
    installation_id: str | None = Field(default=None, min_length=1, max_length=128)
    push_token: str | None = Field(default=None, min_length=1, max_length=255)
    push_platform: Literal["ios", "android"] | None = None


class SignOutRequest(BaseModel):
    installation_id: str = Field(min_length=1, max_length=128)
    push_token: str | None = Field(default=None, min_length=1, max_length=255)


class RegisterPushTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=255)
    platform: Literal["ios", "android"]
    installation_id: str = Field(min_length=1, max_length=128)


class UnregisterPushTokenRequest(BaseModel):
    installation_id: str = Field(min_length=1, max_length=128)


class StationResponse(BaseModel):
    id: str
    name: str
    line: str
    line_color: str

    model_config = ConfigDict(from_attributes=True)


class SupportRequestChecklistItemRequest(BaseModel):
    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    checked: bool = False


class SupportRequestChecklistItemResponse(BaseModel):
    id: int
    code: str
    label: str
    checked: bool


class SupportRequestEventResponse(BaseModel):
    id: int
    type: str
    actor_name: str
    from_status: SupportRequestStatus | None
    to_status: SupportRequestStatus | None
    message: str
    created_at: datetime


class SupportRequestCurrentLocationResponse(BaseModel):
    latitude: float
    longitude: float
    accuracy_meters: float | None
    recorded_at: datetime | None


class SupportRequestListItem(BaseModel):
    id: str
    status: SupportRequestStatus
    origin_station_id: str
    origin_station_name: str
    destination_station_id: str
    destination_station_name: str
    support_types: list[SupportType]
    meeting_point: MeetingPoint
    notes: str
    passenger_name: str
    assigned_staff_name: str | None
    train_car_number: str | None
    created_at: datetime
    cancel_reason: CancelReason | None
    unavailable_reason: UnavailableReason | None
    completion_note: str | None


class SupportRequestDetailResponse(SupportRequestListItem):
    passenger_id: str
    assigned_staff_id: str | None
    current_location: SupportRequestCurrentLocationResponse | None = None
    checklist_items: list[SupportRequestChecklistItemResponse]
    events: list[SupportRequestEventResponse]


class CreateSupportRequestRequest(BaseModel):
    origin_station_id: str = Field(min_length=1)
    destination_station_id: str = Field(min_length=1)
    meeting_point: MeetingPoint
    notes: str = ""
    support_types: list[SupportType] = Field(min_length=1)


class UpdateSupportRequestStatusRequest(BaseModel):
    status: SupportRequestStatus
    train_car_number: str | None = None
    completion_note: str | None = Field(default=None, min_length=1)


class UpdateSupportRequestChecklistRequest(BaseModel):
    items: list[SupportRequestChecklistItemRequest]


class CancelSupportRequestRequest(BaseModel):
    reason: str = Field(min_length=1)


class UnavailableSupportRequestRequest(BaseModel):
    reason: str = Field(min_length=1)
