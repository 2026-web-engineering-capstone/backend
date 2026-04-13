from enum import StrEnum


class Role(StrEnum):
    PASSENGER = "passenger"
    STAFF = "staff"
    DRIVER = "driver"
    ADMIN = "admin"


class SupportRequestStatus(StrEnum):
    SUBMITTED = "submitted"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BOARDED = "boarded"
    AWAITING_DROPOFF = "awaiting_dropoff"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"


class SupportType(StrEnum):
    WHEELCHAIR = "wheelchair"
    VISUAL_GUIDE = "visual-guide"
    BOARDING_RAMP = "boarding-ramp"


class MeetingPoint(StrEnum):
    ELEVATOR = "elevator"
    GATE = "gate"
    INFO_CENTER = "info-center"
    PLATFORM = "platform"
    OTHER = "other"
