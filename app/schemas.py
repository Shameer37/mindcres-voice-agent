
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
)

from app.utils.time import to_ist


Language = Literal["en", "hi", "te"]

CallStatus = Literal[
    "created",
    "in_progress",
    "completed",
    "failed",
    "opted_out",
]

IssueResolution = Literal["yes", "no", "partly"]
CallOutcome = Literal[
    "completed",
    "partial",
    "callback_requested",
    "wrong_person",
    "no_response",
    "opted_out",
    "error",
]


class CustomerRead(BaseModel):
    customer_id: str
    name: str
    mobile_masked: str
    preferred_language: Language
    ticket_id: str
    issue_type: str
    technician_name: str
    visit_date: str
    promised_slot: str


class CallCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=30)


class CallRead(BaseModel):
    id: str
    customer_id: str
    status: CallStatus
    outcome: str | None
    needs_followup: bool | None
    do_not_call: bool
    end_reason: str | None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> datetime:
        return to_ist(value)

    model_config = ConfigDict(from_attributes=True)


class TranscriptTurnRead(BaseModel):
    speaker: str
    text: str
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> datetime:
        return to_ist(value)

    model_config = ConfigDict(from_attributes=True)


class TurnMetricRead(BaseModel):
    turn_number: int
    stt_ms: float | None
    llm_ms: float | None
    tts_first_audio_ms: float | None
    response_latency_ms: float | None
    interrupted_at_ms: float | None

    model_config = ConfigDict(from_attributes=True)


class FeedbackResult(BaseModel):
    identity_confirmed: bool | None = None
    issue_resolved: IssueResolution | None = None
    still_facing: str | None = Field(
        default=None, max_length=2000
    )
    technician_on_time: bool | None = None
    technician_rating: int | None = Field(
        default=None, ge=1, le=5
    )
    nps: int | None = Field(
        default=None, ge=0, le=10
    )
    other_feedback: str | None = Field(
        default=None, max_length=2000
    )

    needs_followup: bool = False
    do_not_call: bool = False
    callback_time: str | None = Field(
        default=None, max_length=100
    )
    unclear_fields: list[str] = Field(default_factory=list)

    outcome: str
    end_reason: str | None = None


class FeedbackAnswers(BaseModel):
    identity_confirmed: bool | None = None
    issue_resolved: IssueResolution | None = None
    still_facing: str | None = Field(default=None, max_length=2000)
    technician_on_time: bool | None = None
    technician_rating: int | None = Field(default=None, ge=1, le=5)
    nps: int | None = Field(default=None, ge=0, le=10)
    other_feedback: str | None = Field(default=None, max_length=2000)


class CallResult(BaseModel):
    call_id: str
    customer_id: str
    outcome: CallOutcome
    end_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime
    duration_sec: float | None = Field(default=None, ge=0)
    answers: FeedbackAnswers
    needs_followup: bool = False
    do_not_call: bool = False
    callback_time: str | None = Field(default=None, max_length=100)
    unclear_fields: list[str] = Field(default_factory=list)

    @field_serializer("started_at", "ended_at")
    def serialize_timestamps(self, value: datetime | None) -> datetime | None:
        return to_ist(value) if value is not None else None


class CallDetailRead(CallRead):
    result: CallResult | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_sec: float | None = None
    transcript: list[TranscriptTurnRead] = Field(
        default_factory=list
    )
    turn_metrics: list[TurnMetricRead] = Field(
        default_factory=list
    )

    @field_serializer("started_at", "ended_at")
    def serialize_detail_timestamps(
        self,
        value: datetime | None,
    ) -> datetime | None:
        return to_ist(value) if value is not None else None
