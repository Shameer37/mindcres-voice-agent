
import pytest

from app.services.conversation_service import ConversationService


def test_identity_must_be_confirmed_before_feedback():
    service = ConversationService()
    service.start("call-1", "C001")

    reply = service.handle_turn("call-1", "I'm not sure")

    assert reply.state == "identity"
    assert "account holder" in reply.text.lower()


def test_wrong_person_ends_call():
    service = ConversationService()
    service.start("call-2", "C001")

    reply = service.handle_turn("call-2", "No, wrong person")

    assert reply.action == "hangup"
    assert reply.result["outcome"] == "wrong_person"


def test_opt_out_ends_call():
    service = ConversationService()
    service.start("call-3", "C001")

    reply = service.handle_turn("call-3", "Please stop calling me")

    assert reply.action == "opt_out"
    assert reply.result["outcome"] == "opted_out"


def test_abusive_call_is_ended_and_marked_opted_out():
    service = ConversationService()
    service.start("call-abusive", "C001")

    reply = service.handle_turn("call-abusive", "Fuck off")

    assert reply.action == "opt_out"
    assert reply.result["outcome"] == "opted_out"


def test_callback_request_ends_call():
    service = ConversationService()
    service.start("call-4", "C001")

    reply = service.handle_turn("call-4", "Please call me tomorrow")

    assert reply.action == "callback"
    assert reply.result["callback_time"] == "tomorrow"
    assert reply.result["needs_followup"] is True
    assert reply.result["do_not_call"] is False


def test_bye_ends_call_without_opt_out_or_rating():
    service = ConversationService()
    service.start("call-bye", "C001")
    service.handle_turn("call-bye", "yes")
    service.handle_turn("call-bye", "yes", extracted={"issue_resolved": "yes"})
    service.handle_turn("call-bye", "no", extracted={"still_facing": "none"})
    service.handle_turn("call-bye", "yes", extracted={"technician_on_time": True})

    reply = service.handle_turn("call-bye", "Bye")

    state = service.get_state("call-bye")
    assert reply.action == "hangup"
    assert reply.result["outcome"] == "caller_requested_end"
    assert reply.result["do_not_call"] is False
    assert state.feedback["technician_rating"] is None
    assert state.unclear_fields == []


def test_explicit_stop_calling_is_opt_out_but_callback_is_not():
    service = ConversationService()
    service.start("call-stop-calling", "C001")
    stop = service.handle_turn("call-stop-calling", "Please stop calling me")
    assert stop.action == "opt_out"
    assert stop.result["do_not_call"] is True

    service.start("call-callback-not-opt-out", "C001")
    callback = service.handle_turn("call-callback-not-opt-out", "Call me tomorrow")
    assert callback.action == "callback"
    assert callback.result["callback_time"] == "tomorrow"
    assert callback.result["do_not_call"] is False


def test_multiple_fields_can_be_extracted_in_one_turn():
    service = ConversationService()
    service.start("call-5", "C001")
    service.handle_turn("call-5", "Yes, I am the account holder")

    reply = service.handle_turn(
        "call-5",
        "The issue is resolved; the technician arrived on time",
        extracted={
            "issue_resolved": "yes",
            "technician_on_time": True,
        },
    )

    state = service.get_state("call-5")
    assert state.feedback["issue_resolved"] == "yes"
    assert state.feedback["technician_on_time"] is True
    assert reply.state == "feedback"


@pytest.mark.parametrize(
    ("language", "greeting_fragment", "question_fragment"),
    [
        ("hi", "नमस्ते", "क्या आपकी इंटरनेट समस्या"),
        ("te", "నమస్కారం", "మీ ఇంటర్నెట్ సమస్య"),
    ],
)
def test_selected_language_propagates_to_greeting_and_questions(
    language, greeting_fragment, question_fragment
):
    service = ConversationService()
    call_id = f"locale-{language}"
    greeting = service.start(call_id, "CUST001", language=language)
    assert greeting_fragment in greeting.text
    reply = service.handle_turn(
        call_id,
        "yes",
        extracted={"identity_confirmed": True},
    )
    assert question_fragment in reply.text


def test_invalid_rating_is_not_accepted():
    service = ConversationService()
    service.start("call-6", "C001")
    service.handle_turn("call-6", "Yes")

    service.handle_turn(
        "call-6",
        "My rating is 8",
        extracted={"technician_rating": 8},
    )

    state = service.get_state("call-6")
    assert state.feedback["technician_rating"] is None


def test_decimal_rating_does_not_advance_until_clarified():
    service = ConversationService()
    call_id = "decimal-rating-retry"
    service.start(call_id, "C001")
    state = service.get_state(call_id)
    state.step = "feedback"
    state.feedback.update({
        "identity_confirmed": True,
        "issue_resolved": "yes",
        "still_facing": "none",
        "technician_on_time": True,
    })
    state.pending_field = "technician_rating"

    retry = service.handle_turn(call_id, "1.3")
    assert state.feedback["technician_rating"] is None
    assert state.pending_field == "technician_rating"
    assert "1 to 5" in retry.text

    next_question = service.handle_turn(call_id, "4", extracted={"technician_rating": 4})
    assert state.feedback["technician_rating"] == 4
    assert state.pending_field == "nps"
    assert "0 to 10" in next_question.text


@pytest.mark.parametrize("phrase", [
    "I have another feedback.",
    "something else to add.",
    "I have more feedback.",
    "I have another comment.",
])
def test_another_feedback_intent_asks_for_details_without_saving_or_retrying(phrase):
    service = ConversationService()
    call_id = "feedback-intent-detail"
    service.start(call_id, "C001")
    state = service.get_state(call_id)
    state.step = "feedback"
    state.feedback.update({
        "identity_confirmed": True,
        "issue_resolved": "no",
        "still_facing": "internet is not working",
        "technician_on_time": False,
        "technician_rating": 1,
        "nps": 0,
    })
    state.pending_field = "other_feedback"
    state.retries["other_feedback"] = 1

    reply = service.handle_turn(call_id, phrase, extracted=None)
    assert "what feedback" in reply.text.lower()
    assert state.feedback["other_feedback"] is None
    assert state.pending_field == "other_feedback"
    assert state.retries["other_feedback"] == 1


def test_caller_can_correct_a_previous_answer():
    service = ConversationService()
    service.start("call-7", "C001")
    service.handle_turn("call-7", "Yes")

    service.handle_turn(
        "call-7",
        "It was resolved",
        extracted={"issue_resolved": "yes"},
    )
    service.handle_turn(
        "call-7",
        "Actually, it is only partly resolved",
        extracted={"issue_resolved": "partly"},
    )

    state = service.get_state("call-7")
    assert state.feedback["issue_resolved"] == "partly"


def test_repeat_request_rephrases_without_spending_retry():
    service = ConversationService()
    service.start("call-repeat", "C001")
    state = service.get_state("call-repeat")

    first = service.handle_turn("call-repeat", "not sure")
    retries_before = state.retries["identity_confirmed"]
    repeated = service.handle_turn("call-repeat", "Could you repeat that?")

    assert first.text != repeated.text
    assert "account holder" in repeated.text.lower()
    assert state.retries["identity_confirmed"] == retries_before
