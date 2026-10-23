from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(
        String(30), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100))
    mobile: Mapped[str] = mapped_column(String(30))
    preferred_language: Mapped[str] = mapped_column(String(2))
    ticket_id: Mapped[str] = mapped_column(String(30), index=True)
    issue_type: Mapped[str] = mapped_column(String(150))
    technician_name: Mapped[str] = mapped_column(String(100))
    visit_date: Mapped[str] = mapped_column(String(20))
    promised_slot: Mapped[str] = mapped_column(String(50))

    calls: Mapped[list["Call"]] = relationship(
        back_populates="customer"
    )


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, index=True
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(30), default="created", index=True
    )
    outcome: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    needs_followup: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, index=True
    )
    do_not_call: Mapped[bool] = mapped_column(
        Boolean, default=False
    )

    # The final validated feedback payload will be stored here.
    result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )

    end_reason: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="calls"
    )
    transcript: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
        order_by="TranscriptTurn.id",
    )
    turn_metrics: Mapped[list["TurnMetric"]] = relationship(
        back_populates="call",
        cascade="all, delete-orphan",
        order_by="TurnMetric.id",
    )

    @property
    def duration_sec(self) -> float | None:
        """Return the completed call duration when both timestamps exist."""
        if self.started_at is None or self.ended_at is None:
            return None
        started = self.started_at
        ended = self.ended_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        return max(0.0, round((ended - started).total_seconds(), 3))


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id"), index=True
    )
    speaker: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    call: Mapped["Call"] = relationship(
        back_populates="transcript"
    )


class TurnMetric(Base):
    __tablename__ = "turn_metrics"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    call_id: Mapped[str] = mapped_column(
        ForeignKey("calls.id"), index=True
    )
    turn_number: Mapped[int] = mapped_column(Integer)

    stt_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    llm_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    tts_first_audio_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    response_latency_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    interrupted_at_ms: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    call: Mapped["Call"] = relationship(
        back_populates="turn_metrics"
    )
