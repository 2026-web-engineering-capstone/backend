from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import MeetingPoint, Role, SupportRequestStatus, SupportType


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    line: Mapped[str] = mapped_column(String(128))
    line_color: Mapped[str] = mapped_column(String(16))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"USR-{uuid4().hex[:12]}"
    )
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[Role] = mapped_column(Enum(Role), index=True)
    station_id: Mapped[str | None] = mapped_column(ForeignKey("stations.id"), nullable=True)

    station: Mapped[Station | None] = relationship()


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"SES-{uuid4().hex}"
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc) + timedelta(days=7),
        index=True,
    )

    user: Mapped[User] = relationship()


class UserPushToken(Base):
    __tablename__ = "user_push_tokens"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"PUSH-{uuid4().hex[:16]}"
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped[User] = relationship()


class SupportRequest(Base):
    __tablename__ = "support_requests"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"REQ-{uuid4().hex[:12].upper()}"
    )
    passenger_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    assigned_staff_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    origin_station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"))
    destination_station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"))
    meeting_point: Mapped[MeetingPoint] = mapped_column(Enum(MeetingPoint))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[SupportRequestStatus] = mapped_column(
        Enum(SupportRequestStatus),
        default=SupportRequestStatus.SUBMITTED,
        index=True,
    )
    train_car_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unavailable_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    passenger: Mapped[User] = relationship(foreign_keys=[passenger_user_id])
    assigned_staff: Mapped[User | None] = relationship(foreign_keys=[assigned_staff_user_id])
    origin_station: Mapped[Station] = relationship(foreign_keys=[origin_station_id])
    destination_station: Mapped[Station] = relationship(foreign_keys=[destination_station_id])
    support_types: Mapped[list[SupportRequestSupportType]] = relationship(
        back_populates="support_request", cascade="all, delete-orphan"
    )
    checklist_items: Mapped[list[SupportRequestChecklistItem]] = relationship(
        back_populates="support_request",
        cascade="all, delete-orphan",
        order_by=lambda: SupportRequestChecklistItem.id.asc(),
    )
    events: Mapped[list[SupportRequestEvent]] = relationship(
        back_populates="support_request",
        cascade="all, delete-orphan",
        order_by=lambda: (SupportRequestEvent.created_at.asc(), SupportRequestEvent.id.asc()),
    )


class SupportRequestSupportType(Base):
    __tablename__ = "support_request_support_types"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("support_requests.id"), index=True)
    support_type: Mapped[SupportType] = mapped_column(Enum(SupportType))

    support_request: Mapped[SupportRequest] = relationship(back_populates="support_types")


class SupportRequestChecklistItem(Base):
    __tablename__ = "support_request_checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("support_requests.id"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    checked: Mapped[bool] = mapped_column(Boolean, default=False)

    support_request: Mapped[SupportRequest] = relationship(back_populates="checklist_items")


class SupportRequestEvent(Base):
    __tablename__ = "support_request_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("support_requests.id"), index=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    from_status: Mapped[SupportRequestStatus | None] = mapped_column(
        Enum(SupportRequestStatus), nullable=True
    )
    to_status: Mapped[SupportRequestStatus | None] = mapped_column(
        Enum(SupportRequestStatus), nullable=True
    )
    message: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    support_request: Mapped[SupportRequest] = relationship(back_populates="events")
    actor: Mapped[User] = relationship()
