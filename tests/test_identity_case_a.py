
from app.services.conversation_service import ConversationService


def test_name_and_feedback_without_identity_confirmation():
    service = ConversationService()
    call_id = "case-a-test"

    service.start(call_id=call_id, customer_id="customer-1")

    reply = service.handle_turn(
        call_id=call_id,
        user_text=(
            "Hello, I am Rahul. The technician fixed my internet "
            "problem, and my connection is working fine now."
        ),
        extracted={
            "issue_resolved": "yes",
            "still_facing": "none",
        },
    )

    state = service.get_state(call_id)

    assert reply.action == "continue"
    assert state.step == "identity"
    assert state.terminal is False
    assert state.feedback["identity_confirmed"] is None
    assert state.feedback["issue_resolved"] is None
    assert state.feedback["still_facing"] is None
    assert "account holder" in reply.text.lower()
