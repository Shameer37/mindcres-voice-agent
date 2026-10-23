from app.api.websocket import fast_extract_answer
import pytest


def test_spoken_rating_is_extracted_without_llm():
    assert fast_extract_answer(
        "I would rate the technician five out of five.",
        "technician_rating",
    ) == {"technician_rating": 5}


def test_spoken_nps_is_extracted_without_llm():
    assert fast_extract_answer(
        "I would recommend SkyFiber nine out of ten.",
        "nps",
    ) == {"nps": 9}


def test_nps_zero_remains_a_valid_score():
    assert fast_extract_answer("0", "nps") == {"nps": 0}


def test_full_sentence_punctuality_answer_uses_deterministic_parser():
    assert fast_extract_answer(
        "No, the technician did not arrive on time.",
        "technician_on_time",
    ) == {"technician_on_time": False}


def test_out_of_range_rating_is_not_accepted():
    assert fast_extract_answer(
        "I would rate the technician seven out of ten.",
        "technician_rating",
    ) is None


def test_hindi_short_answers_and_spoken_rating_are_extracted():
    assert fast_extract_answer("हाँ", "identity_confirmed", "hi") == {"identity_confirmed": True}
    assert fast_extract_answer("नहीं", "issue_resolved", "hi") == {"issue_resolved": "no"}
    assert fast_extract_answer("पाँच", "technician_rating", "hi") == {"technician_rating": 5}


def test_telugu_short_answers_and_spoken_rating_are_extracted():
    assert fast_extract_answer("అవును", "identity_confirmed", "te") == {"identity_confirmed": True}
    assert fast_extract_answer("లేదు", "still_facing", "te") == {"still_facing": "none"}
    assert fast_extract_answer("ఐదు", "technician_rating", "te") == {"technician_rating": 5}


def test_telugu_problem_description_is_preserved_from_transcript():
    phrase = "ఇంటర్నెట్ తరచుగా డిస్‌కనెక్ట్ అవుతోంది"
    assert fast_extract_answer(phrase, "still_facing", "te") == {"still_facing": phrase}


@pytest.mark.parametrize("phrase", [
    "fully restored", "It's working now", "yes, completely",
    "resolved", "fixed",
])
def test_issue_resolved_affirmative_phrases_are_contextual_and_fast(phrase):
    assert fast_extract_answer(phrase, "issue_resolved") == {"issue_resolved": "yes"}


@pytest.mark.parametrize("phrase", ["partly resolved", "partly", "somewhat resolved"])
def test_issue_resolved_partial_phrases(phrase):
    assert fast_extract_answer(phrase, "issue_resolved") == {"issue_resolved": "partly"}


@pytest.mark.parametrize("phrase", ["still not working", "not resolved", "not fixed", "not fully restored"])
def test_issue_resolved_negative_phrases(phrase):
    assert fast_extract_answer(phrase, "issue_resolved") == {"issue_resolved": "no"}


@pytest.mark.parametrize("phrase", ["No", "No problems", "Everything is working"])
def test_still_facing_short_or_no_problem_answers_use_question_context(phrase):
    assert fast_extract_answer(phrase, "still_facing") == {"still_facing": "none"}


@pytest.mark.parametrize("phrase", [
    "It's still disconnecting", "The internet is slow", "Connection keeps dropping",
])
def test_still_facing_descriptions_are_preserved(phrase):
    assert fast_extract_answer(phrase, "still_facing") == {"still_facing": phrase}


def test_generic_yes_no_is_not_applied_to_issue_description_or_resolution_blindly():
    assert fast_extract_answer("No", "issue_resolved") == {"issue_resolved": "no"}
    assert fast_extract_answer("Yes", "still_facing") is None


@pytest.mark.parametrize("phrase,expected", [
    ("five", 5), ("5", 5), ("five out of five", 5),
    ("I would give it a four out of 5", 4),
])
def test_spoken_technician_rating_variations(phrase, expected):
    assert fast_extract_answer(phrase, "technician_rating") == {"technician_rating": expected}


def test_bye_is_never_parsed_as_a_rating_or_opt_out():
    from app.services.conversation_service import ConversationService
    service = ConversationService()
    service.start("bye-rating", "C001")
    service.handle_turn("bye-rating", "yes")
    service.handle_turn("bye-rating", "yes", extracted={"issue_resolved": "yes"})
    service.handle_turn("bye-rating", "no", extracted={"still_facing": "none"})
    service.handle_turn("bye-rating", "yes", extracted={"technician_on_time": True})
    service.handle_turn("bye-rating", "bye")
    state = service.get_state("bye-rating")
    assert state.terminal is True
    assert state.feedback["technician_rating"] is None
    assert state.unclear_fields == []
    assert state.step == "ended"


def test_identity_natural_affirmative_variation_is_accepted():
    assert fast_extract_answer("And I am the account holder", "identity_confirmed") == {"identity_confirmed": True}


@pytest.mark.parametrize("phrase", ["yes", "Yeah.", "YEP!", "yes, I am", "correct", "that's right"])
def test_identity_affirmative_variants_are_question_aware(phrase):
    assert fast_extract_answer(phrase, "identity_confirmed") == {"identity_confirmed": True}


@pytest.mark.parametrize("phrase", ["no", "NOPE."])
def test_identity_negative_variants_are_question_aware(phrase):
    assert fast_extract_answer(phrase, "identity_confirmed") == {"identity_confirmed": False}


@pytest.mark.parametrize("phrase", ["yes", "yeah", "yep", "correct", "that's right"])
def test_resolution_affirmatives_are_accepted_only_for_resolution_question(phrase):
    assert fast_extract_answer(phrase, "issue_resolved") == {"issue_resolved": "yes"}
    assert fast_extract_answer(phrase, "still_facing") is None


@pytest.mark.parametrize("phrase", ["no", "nope"])
def test_resolution_negatives_are_accepted(phrase):
    assert fast_extract_answer(phrase, "issue_resolved") == {"issue_resolved": "no"}


@pytest.mark.parametrize("phrase", ["1.3", "I would rate the technician 1.3", "one point three"])
def test_decimal_rating_is_not_truncated_or_sent_as_clear_answer(phrase):
    assert fast_extract_answer(phrase, "technician_rating") is None


@pytest.mark.parametrize("phrase", ["9.5", "I would give the score 9.5", "nine point five"])
def test_decimal_nps_is_not_truncated_or_sent_as_clear_answer(phrase):
    assert fast_extract_answer(phrase, "nps") is None


def test_empty_and_ambiguous_identity_transcripts_are_not_answers():
    assert fast_extract_answer("", "identity_confirmed") is None
    assert fast_extract_answer("maybe", "identity_confirmed") is None
    # This is the transcript persisted by the call; without source audio it
    # cannot establish whether STT or the caller produced this word.
    assert fast_extract_answer("here", "identity_confirmed") is None


def test_feedback_intent_is_not_mistaken_for_feedback_content():
    assert fast_extract_answer("I have another feedback.", "other_feedback") == {"other_feedback": None}


@pytest.mark.parametrize("phrase", [
    "something else to add",
    "I have more feedback",
    "I have another comment",
    "another comment",
])
def test_additional_feedback_intent_is_not_saved_as_content(phrase):
    assert fast_extract_answer(phrase, "other_feedback") == {"other_feedback": None}


@pytest.mark.parametrize("phrase,expected", [("Yeah", True), ("Yep!", True), ("Correct.", True), ("No", False), ("Nope.", False)])
def test_technician_arrival_confirmation_uses_yes_no_context(phrase, expected):
    assert fast_extract_answer(phrase, "technician_on_time") == {"technician_on_time": expected}


def test_yes_identity_answer_does_not_call_llm():
    import asyncio
    from app.api.websocket import extract_turn_candidates
    from app.services.conversation_service import ConversationService

    class NeverCallLLM:
        called = False

        async def extract(self, **kwargs):
            self.called = True
            raise AssertionError("clear identity answer should not call LLM")

    service = ConversationService()
    service.start("affirm-fast", "CUST001")
    state = service.get_state("affirm-fast")
    llm = NeverCallLLM()
    extracted, attempted, _, parser, _ = asyncio.run(
        extract_turn_candidates("Yeah.", state, "identity_confirmed", llm)
    )
    assert extracted["identity_confirmed"] is True
    assert attempted is False
    assert parser == "identity_rules"
    assert llm.called is False


def test_decimal_rating_stays_pending_and_skips_llm():
    import asyncio
    from app.api.websocket import extract_turn_candidates
    from app.services.conversation_service import ConversationService

    class NeverCallLLM:
        called = False

        async def extract(self, **kwargs):
            self.called = True
            raise AssertionError("invalid decimal must be clarified deterministically")

    service = ConversationService()
    service.start("decimal-retry", "CUST001")
    state = service.get_state("decimal-retry")
    state.step = "feedback"
    state.feedback.update({
        "identity_confirmed": True,
        "issue_resolved": "yes",
        "still_facing": "none",
        "technician_on_time": True,
    })
    state.pending_field = "technician_rating"
    llm = NeverCallLLM()
    extracted, attempted, _, parser, _ = asyncio.run(
        extract_turn_candidates("I would rate the technician 1.3", state, "technician_rating", llm)
    )
    reply = service.handle_turn("decimal-retry", "I would rate the technician 1.3", extracted)
    assert extracted is None
    assert attempted is False
    assert parser == "numeric_validation"
    assert llm.called is False
    assert state.feedback["technician_rating"] is None
    assert state.pending_field == "technician_rating"
    assert "1 to 5" in reply.text


def test_feedback_intent_skips_llm_and_does_not_save_the_intent_phrase():
    import asyncio
    from app.api.websocket import extract_turn_candidates
    from app.services.conversation_service import ConversationService

    class NeverCallLLM:
        called = False

        async def extract(self, **kwargs):
            self.called = True
            raise AssertionError("feedback-intent phrase needs a direct clarification")

    service = ConversationService()
    service.start("feedback-intent-fast", "CUST001")
    state = service.get_state("feedback-intent-fast")
    state.step = "feedback"
    state.feedback.update({"identity_confirmed": True})
    state.pending_field = "other_feedback"
    llm = NeverCallLLM()
    extracted, attempted, _, parser, _ = asyncio.run(
        extract_turn_candidates("I have another feedback.", state, "other_feedback", llm)
    )
    reply = service.handle_turn("feedback-intent-fast", "I have another feedback.", extracted)
    assert extracted is None
    assert attempted is False
    assert parser == "question_rules"
    assert llm.called is False
    assert "what feedback" in reply.text.lower()
    assert state.feedback["other_feedback"] is None


def test_unclear_feedback_fallback_does_not_invent_an_answer():
    import asyncio
    from app.api.websocket import extract_turn_candidates
    from app.services.conversation_service import ConversationService

    class EmptyLLM:
        captured = None

        async def extract(self, **kwargs):
            self.captured = kwargs
            return None

    service = ConversationService()
    service.start("unclear-feedback", "CUST001")
    state = service.get_state("unclear-feedback")
    state.step = "feedback"
    state.feedback["identity_confirmed"] = True
    state.pending_field = "other_feedback"
    llm = EmptyLLM()
    extracted, attempted, _, parser, reason = asyncio.run(
        extract_turn_candidates("Force, experience, and war.", state, "other_feedback", llm)
    )
    assert attempted is True
    assert parser == "ollama_extraction"
    assert reason == "question_rules_found_no_confident_answer"
    assert llm.captured["user_text"] == "Force, experience, and war."
    assert extracted is None
