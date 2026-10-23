
from app.services.conversation_service import ConversationService
from app.api.websocket import fast_extract_answer


def test_decimal_nps_is_rejected_and_question_remains_pending():
    service = ConversationService()
    call_id = "nps-decimal-retry"
    service.start(call_id=call_id, customer_id="customer-1")
    state = service.get_state(call_id)
    state.step = "feedback"
    state.feedback.update({
        "identity_confirmed": True,
        "issue_resolved": "yes",
        "still_facing": "none",
        "technician_on_time": True,
        "technician_rating": 5,
    })
    state.pending_field = "nps"

    assert fast_extract_answer("9.5", "nps") is None
    reply = service.handle_turn(call_id, "9.5", extracted=None)
    assert state.feedback["nps"] is None
    assert state.pending_field == "nps"
    assert "0 to 10" in reply.text


def test_invalid_nps_is_reasked_before_becoming_unclear():
    service = ConversationService()
    call_id = "nps-retry-test"

    service.start(call_id=call_id, customer_id="customer-1")
    state = service.get_state(call_id)

    # Simulate identity confirmation.
    state.feedback["identity_confirmed"] = True
    state.step = "feedback"

    # Fill all fields before NPS so it is the next question.
    state.feedback.update({
        "issue_resolved": "yes",
        "still_facing": "none",
        "technician_on_time": True,
        "technician_rating": 5,
    })

    first_question = service.handle_turn(
        call_id, "5", extracted={"technician_rating": 5}
    )
    assert "0 to 10" in first_question.text

    first_invalid = service.handle_turn(
        call_id, "again", extracted={}
    )
    assert first_invalid.action == "continue"
    assert "0 to 10" in first_invalid.text
    assert state.feedback["nps"] is None
    assert "nps" not in state.unclear_fields

    second_invalid = service.handle_turn(
        call_id, "I don't know", extracted={}
    )
    assert "nps" in state.unclear_fields
    assert state.feedback["nps"] is None
    assert "other feedback" in second_invalid.text.lower()
