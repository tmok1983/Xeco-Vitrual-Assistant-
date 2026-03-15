from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EVSupportMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str = Field(min_length=1, max_length=4000)
    at: datetime


class EVSupportSession(BaseModel):
    session_id: str
    channel_user_id: str
    source_channel: str = "line"
    language: str = "th-TH"
    created_at: datetime
    updated_at: datetime
    messages: list[EVSupportMessage] = Field(default_factory=list)
    last_intent: str | None = None
    last_station_id: str | None = None
    last_media_summary: str | None = None
    last_media_type: str | None = None
    human_handoff_active: bool = False
    handoff_requested_at: datetime | None = None
    handoff_reason: str | None = None


class EVChargingContext(BaseModel):
    station_id: str | None = None
    charger_type: str | None = None
    vehicle_model: str | None = None
    issue_type: str | None = None
    battery_percent: int | None = Field(default=None, ge=0, le=100)


class EVSupportRequest(BaseModel):
    session_id: str
    user_id: str
    message_text: str = Field(min_length=1, max_length=4000)
    channel: str = "line"
    language: str = "th-TH"
    context: EVChargingContext = Field(default_factory=EVChargingContext)


class EVSupportResponse(BaseModel):
    session_id: str
    user_id: str
    detected_language: str
    detected_intent: str
    reply_text: str
    escalate_to_human: bool = False
    knowledge_hits: list[str] = Field(default_factory=list)


class LineUserSource(BaseModel):
    type: Literal["user", "group", "room"]
    userId: str | None = None
    groupId: str | None = None
    roomId: str | None = None


class LineMessageBody(BaseModel):
    id: str
    type: Literal["text", "image", "audio"]
    text: str | None = None
    duration: int | None = None


class LineWebhookEvent(BaseModel):
    type: str
    replyToken: str | None = None
    timestamp: int
    source: LineUserSource
    message: LineMessageBody | None = None


class LineWebhookPayload(BaseModel):
    destination: str | None = None
    events: list[LineWebhookEvent] = Field(default_factory=list)


class LineReplyMessage(BaseModel):
    type: str = "text"
    text: str


class LineReplyRequest(BaseModel):
    replyToken: str
    messages: list[LineReplyMessage]


class LinePushRequest(BaseModel):
    to: str
    messages: list[LineReplyMessage]
