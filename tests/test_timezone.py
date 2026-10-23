
from datetime import datetime, timezone

from app.schemas import CallRead, TranscriptTurnRead


def test_call_timestamp_serializes_as_ist():
    utc_timestamp = datetime(
        2026, 10, 20, 7, 35, 34, tzinfo=timezone.utc
    )

    call = CallRead(
        id="test-call",
        customer_id="CUST001",
        status="created",
        outcome=None,
        needs_followup=None,
        do_not_call=False,
        end_reason=None,
        created_at=utc_timestamp,
    )

    data = call.model_dump(mode="json")

    assert data["created_at"] == "2026-10-20T13:05:34+05:30"


def test_transcript_timestamp_serializes_as_ist():
    utc_timestamp = datetime(
        2026, 10, 20, 7, 35, 34, tzinfo=timezone.utc
    )

    turn = TranscriptTurnRead(
        speaker="caller",
        text="Hello",
        created_at=utc_timestamp,
    )

    data = turn.model_dump(mode="json")

    assert data["created_at"] == "2026-10-20T13:05:34+05:30"
