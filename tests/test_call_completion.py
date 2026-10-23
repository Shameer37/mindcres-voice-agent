from datetime import datetime, timezone

from app.api.websocket import conversation_service, finish_call, get_partial_result
from app.models import Call
from app.schemas import CallDetailRead


def test_finish_call_validates_partial_result_and_persists_opt_out(
    test_session_factory,
    test_customer,
):
    with test_session_factory() as db:
        call = Call(
            id="opt-out-test",
            customer_id=test_customer,
            started_at=datetime.now(timezone.utc),
        )
        db.add(call)
        db.commit()

        finish_call(
            db,
            call,
            status="opted_out",
            reason="opt_out",
            result={
                "identity_confirmed": None,
                "outcome": "opted_out",
                "needs_followup": False,
                "unclear_fields": [],
                "callback_time": None,
            },
        )

        db.refresh(call)
        assert call.do_not_call is True
        assert call.result["do_not_call"] is True
        assert call.result["outcome"] == "opted_out"
        assert call.result["answers"]["identity_confirmed"] is None
        assert call.result["end_reason"] == "opt_out"
        detail = CallDetailRead.model_validate(call)
        assert detail.duration_sec is not None
        assert detail.result.answers.identity_confirmed is None


def test_finish_call_is_idempotent_for_terminal_outcomes(
    test_session_factory,
    test_customer,
):
    with test_session_factory() as db:
        call = Call(
            id="repeat-terminal-test",
            customer_id=test_customer,
            started_at=datetime.now(timezone.utc),
        )
        db.add(call)
        db.commit()
        wrong_person = {
            "identity_confirmed": False,
            "outcome": "wrong_person",
            "needs_followup": False,
            "unclear_fields": [],
        }

        finish_call(db, call, "completed", "wrong_person", wrong_person)
        first_ended_at = call.ended_at
        finish_call(
            db,
            call,
            "completed",
            "caller_hangup",
            {"identity_confirmed": None, "outcome": "caller_hangup"},
        )

        db.refresh(call)
        assert call.end_reason == "wrong_person"
        assert call.ended_at == first_ended_at
        assert call.result["outcome"] == "wrong_person"
        assert call.result["answers"]["identity_confirmed"] is False


def test_finish_call_preserves_saved_answers_as_partial_not_no_response(
    test_session_factory,
    test_customer,
):
    with test_session_factory() as db:
        call = Call(
            id="no-response-terminal-test",
            customer_id=test_customer,
            started_at=datetime.now(timezone.utc),
        )
        db.add(call)
        db.commit()
        finish_call(
            db,
            call,
            "completed",
            "no_input",
            {
                "identity_confirmed": True,
                "outcome": "no_response",
                "needs_followup": False,
                "unclear_fields": [],
            },
        )
        db.refresh(call)
        assert call.outcome == "partial"
        assert call.needs_followup is False
        assert call.result["end_reason"] == "no_input"


def test_finish_call_persists_caller_hangup_as_partial(
    test_session_factory,
    test_customer,
):
    with test_session_factory() as db:
        call = Call(
            id="caller-hangup-terminal-test",
            customer_id=test_customer,
            started_at=datetime.now(timezone.utc),
        )
        db.add(call)
        db.commit()
        finish_call(
            db,
            call,
            "completed",
            "caller_hangup",
            {
                "identity_confirmed": True,
                "issue_resolved": None,
                "outcome": "caller_hangup",
                "needs_followup": True,
                "unclear_fields": [],
            },
        )
        db.refresh(call)
        assert call.outcome == "partial"
        assert call.end_reason == "caller_hangup"
        assert call.result["answers"]["identity_confirmed"] is True
        assert call.result["answers"]["issue_resolved"] is None


def test_valid_feedback_survives_later_tts_failure(
    test_session_factory,
    test_customer,
):
    call_id = "tts-failure-keeps-feedback"
    conversation_service.start(call_id, test_customer)
    conversation_service.handle_turn(
        call_id, "Yes", extracted={"identity_confirmed": True}
    )
    conversation_service.handle_turn(
        call_id, "Fully restored", extracted={"issue_resolved": "yes"}
    )

    with test_session_factory() as db:
        call = Call(
            id=call_id,
            customer_id=test_customer,
            started_at=datetime.now(timezone.utc),
        )
        db.add(call)
        db.commit()
        # This is the same state snapshot used by terminal error paths after
        # a downstream TTS task has failed.
        partial = get_partial_result(call_id, outcome="error")
        finish_call(db, call, status="failed", reason="tts_error", result=partial)
        db.refresh(call)
        assert call.result["answers"]["identity_confirmed"] is True
        assert call.result["answers"]["issue_resolved"] == "yes"
        assert call.result["outcome"] == "error"

    conversation_service.forget(call_id)
