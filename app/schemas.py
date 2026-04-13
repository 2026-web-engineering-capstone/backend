from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import MeetingPoint, Role, SupportRequestStatus, SupportType


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


class StationResponse(BaseModel):
    id: str
    name: str
    line: str
    line_color: str

    model_config = ConfigDict(from_attributes=True)


class SupportRequestEventResponse(BaseModel):
    id: int
    type: str
    actor_name: str
    from_status: SupportRequestStatus | None
    to_status: SupportRequestStatus | None
    message: str
    created_at: datetime


class SupportRequestListItem(BaseModel):
    id: str
    status: SupportRequestStatus
    origin_station_name: str
    destination_station_name: str
    support_types: list[SupportType]
    meeting_point: MeetingPoint
    notes: str
    passenger_name: str
    assigned_staff_name: str | None
    train_car_number: str | None
    created_at: datetime


class SupportRequestDetailResponse(SupportRequestListItem):
    passenger_id: str
    assigned_staff_id: str | None
    cancel_reason: str | None
    unavailable_reason: str | None
    completion_note: str | None
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


class CancelSupportRequestRequest(BaseModel):
    reason: str = Field(min_length=1)


class UnavailableSupportRequestRequest(BaseModel):
    reason: str = Field(min_length=1)
