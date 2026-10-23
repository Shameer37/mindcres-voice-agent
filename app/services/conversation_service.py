from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


FEEDBACK_FIELDS = (
    "issue_resolved",
    "still_facing",
    "technician_on_time",
    "technician_rating",
    "nps",
    "other_feedback",
)

INITIAL_FEEDBACK: dict[str, Any] = {
    "identity_confirmed": None,
    "issue_resolved": None,
    "still_facing": None,
    "technician_on_time": None,
    "technician_rating": None,
    "nps": None,
    "other_feedback": None,
}


@dataclass
class ConversationState:
    call_id: str
    customer_id: str
    language: str = "en"
    step: str = "identity"
    feedback: dict[str, Any] = field(
        default_factory=lambda: INITIAL_FEEDBACK.copy()
    )
    unclear_fields: list[str] = field(default_factory=list)
    retries: dict[str, int] = field(default_factory=dict)
    pending_field: str | None = None
    turn_number: int = 0
    callback_time: str | None = None
    terminal: bool = False


@dataclass
class ConversationReply:
    text: str
    action: str = "continue"
    state: str = "identity"
    result: dict[str, Any] | None = None


class ConversationService:
    """Controls call flow and validates extracted feedback values."""

    MAX_RETRIES = 2

    def __init__(self) -> None:
        # In-memory state for development.
        # Persist final results through the WebSocket/database layer.
        self._states: dict[str, ConversationState] = {}

    def start(
        self,
        call_id: str,
        customer_id: str,
        language: str = "en",
    ) -> ConversationReply:
        state = ConversationState(
            call_id=call_id,
            customer_id=customer_id,
            language=language if language in {"en", "hi", "te"} else "en",
        )
        self._states[call_id] = state

        return ConversationReply(
            text={
                "en": "Hello, I'm calling from SkyFiber Broadband to ask about your recent service experience. Am I speaking with the account holder?",
                "hi": "नमस्ते, मैं स्काईफ़ाइबर ब्रॉडबैंड से आपकी हाल की सेवा के बारे में प्रतिक्रिया लेने के लिए कॉल कर रहा हूँ। क्या आप खाताधारक हैं?",
                "te": "నమస్కారం, మీ ఇటీవలి సేవ గురించి అభిప్రాయం తెలుసుకోవడానికి స్కైఫైబర్ బ్రాడ్‌బ్యాండ్ నుండి ఫోన్ చేస్తున్నాను. మీరు ఖాతాదారులా?",
            }[state.language],
            state=state.step,
        )

    def get_state(self, call_id: str) -> ConversationState:
        try:
            return self._states[call_id]
        except KeyError as exc:
            raise ValueError(
                f"No conversation state found for call {call_id}"
            ) from exc

    def forget(self, call_id: str) -> None:
        """Release in-memory state after a WebSocket call has ended."""
        self._states.pop(call_id, None)

    def handle_turn(
        self,
        call_id: str,
        user_text: str,
        extracted: dict[str, Any] | None = None,
    ) -> ConversationReply:
        state = self.get_state(call_id)

        if state.terminal:
            return ConversationReply(
                text="This call has already ended.",
                action="hangup",
                state=state.step,
            )

        text = user_text.strip()

        if not text:
            return ConversationReply(
                text="Sorry, I didn't catch that. Could you repeat?",
                state=state.step,
            )

        # Repetition is a conversational repair, not a failed answer. Keep
        # retry counts unchanged and repeat the current goal in simpler words.
        if self._is_repeat(text):
            field = (
                "identity_confirmed"
                if state.step == "identity"
                else state.pending_field
            )
            return ConversationReply(
                text=self._repeat_question(field),
                state=state.step,
            )

        state.turn_number += 1

        # Opt-out requests take priority over other responses.
        if self._is_opt_out(text):
            state.terminal = True
            state.step = "opted_out"

            return ConversationReply(
                text=(
                    "I understand. We'll record your request not to "
                    "receive further feedback calls. Goodbye."
                ),
                action="opt_out",
                state=state.step,
                result=self._result(
                    state,
                    outcome="opted_out",
                ),
            )

        # Respect a caller's request to end the conversation.
        if self._is_abusive_or_stop(text):
            state.terminal = True
            state.step = "ended"

            return ConversationReply(
                text="I understand. I'll end the call now. Goodbye.",
                action="hangup",
                state=state.step,
                result=self._result(
                    state,
                    outcome="caller_requested_end",
                ),
            )

        # Callback requests end the current call.
        if self._is_callback(text):
            callback_time = self._extract_callback_time(text)

            state.callback_time = callback_time
            state.terminal = True
            state.step = "callback_requested"

            result = self._result(
                state,
                outcome="callback_requested",
            )
            result["needs_followup"] = True

            return ConversationReply(
                text=(
                    "Of course. We'll record your request for a callback. "
                    "Thank you, goodbye."
                ),
                action="callback",
                state=state.step,
                result=result,
            )

        if state.step == "identity":
            return self._handle_identity(
                state,
                text,
                extracted,
            )

        if state.step == "feedback":
            return self._handle_feedback(
                state,
                text,
                extracted,
            )

        return ConversationReply(
            text="Thank you. Goodbye.",
            action="hangup",
            state=state.step,
            result=self._result(
                state,
                outcome="completed",
            ),
        )

    # ---------------------------------------------------------
    # Identity verification
    # ---------------------------------------------------------

    def _handle_identity(
        self,
        state: ConversationState,
        text: str,
        extracted: dict[str, Any] | None,
    ) -> ConversationReply:

        identity = (
            extracted.get("identity_confirmed")
            if extracted is not None
            else self._parse_yes_no(text, state.language)
        )

        # Explicitly confirmed that this is the wrong person.
        if identity is False:
            state.feedback["identity_confirmed"] = False
            state.terminal = True
            state.step = "wrong_person"

            return ConversationReply(
                text={"en": "Sorry for the inconvenience. Goodbye.", "hi": "असुविधा के लिए क्षमा करें। नमस्कार।", "te": "అసౌకర్యానికి క్షమించండి. నమస్కారం."}[state.language],
                action="hangup",
                state=state.step,
                result=self._result(
                    state,
                    outcome="wrong_person",
                ),
            )

        # A name or an ambiguous statement is not identity confirmation.
        if identity is not True:
            return self._retry(
                state,
                "identity_confirmed",
                (
                    self._question_for("identity_confirmed", state.language)
                ),
            )

        # Identity has now been explicitly confirmed.
        state.feedback["identity_confirmed"] = True
        state.step = "feedback"

        # IMPORTANT:
        # Capture service feedback if it was included in the same utterance.
        # Identity confirmation is checked before any feedback is processed.
        if extracted is not None:
            feedback_candidates = {
                key: value
                for key, value in extracted.items()
                if key in FEEDBACK_FIELDS
            }
        else:
            feedback_candidates = self._basic_extract(text)

        # If an extractor only returned identity, use the basic parser
        # to check whether the caller also provided feedback.
        if not feedback_candidates:
            feedback_candidates = self._basic_extract(text)

        if feedback_candidates:
            return self._handle_feedback(
                state,
                text,
                feedback_candidates,
            )

        # No feedback was included, so ask the first feedback question.
        return ConversationReply(
            text={
                    "en": "Thank you. Has your internet issue been resolved? You can also tell me if you're still experiencing a problem.",
                    "hi": "धन्यवाद। क्या आपकी इंटरनेट समस्या हल हो गई है? यदि अभी भी समस्या है, तो कृपया बताइए।",
                    "te": "ధన్యవాదాలు. మీ ఇంటర్నెట్ సమస్య పరిష్కారమైందా? ఇంకా సమస్య ఉంటే దయచేసి వివరించండి.",
            }[state.language],
            state=state.step,
        )

    # ---------------------------------------------------------
    # Feedback collection
    # ---------------------------------------------------------

    
    def _handle_feedback(
        self,
        state: ConversationState,
        text: str,
        extracted: dict[str, Any] | None,
    ) -> ConversationReply:
        """Validate feedback and retry only after invalid answers."""

        normalized = text.strip().lower().strip(" .,!?'\"")
        pending = state.pending_field

        if extracted is not None:
            candidates = dict(extracted)
        else:
            candidates = self._basic_extract(text)

        # This states an intent to add feedback, not the feedback itself.
        # Ask for its content without saving this phrase or consuming a retry.
        if pending == "other_feedback" and re.fullmatch(
            r"(?:(?:i have|i've got|there is|there's)\s+(?:another|more|some)\s+(?:feedback|comments?)|something else to add|(?:another|more)\s+(?:feedback|comments?))",
            normalized,
        ):
            return ConversationReply(
                text={
                    "en": "What feedback would you like to share?",
                    "hi": "आप कौन-सी प्रतिक्रिया साझा करना चाहेंगे?",
                    "te": "మీరు ఏ అభిప్రాయాన్ని పంచుకోవాలనుకుంటున్నారు?",
                }[state.language],
                state=state.step,
            )

        # Handle short answers using the question currently being asked.
        if pending == "still_facing" and normalized in {
            "no", "nope", "nah", "not anymore"
        }:
            candidates["still_facing"] = "none"

        if pending == "other_feedback" and normalized in {
            "no", "nope", "nah", "none", "nothing", "no thanks"
        }:
            candidates["other_feedback"] = "none"

        corrected = self._validated_fields(candidates)

        # Save every valid extracted field, including multiple answers
        # provided in one utterance.
        for field_name, value in corrected.items():
            state.feedback[field_name] = value

            if field_name in state.unclear_fields:
                state.unclear_fields.remove(field_name)

            state.retries.pop(field_name, None)

        # Count a failure only if the caller was answering a question
        # that had already been asked.
        if pending is not None:
            if state.feedback.get(pending) is not None:
                state.pending_field = None
            else:
                attempts = state.retries.get(pending, 0) + 1
                state.retries[pending] = attempts

                if attempts < self.MAX_RETRIES:
                    return ConversationReply(
                        text=(
                            {"en": "Sorry, I didn't catch a valid answer. ", "hi": "क्षमा करें, सही उत्तर समझ नहीं आया। ", "te": "క్షమించండి, సరైన సమాధానం వినిపించలేదు. "}[state.language]
                            + self._question_for(pending, state.language)
                        ),
                        state=state.step,
                    )

                # Two invalid responses: preserve null and move on.
                if pending not in state.unclear_fields:
                    state.unclear_fields.append(pending)

                state.pending_field = None

        # Find the next unanswered field that has not exhausted retries.
        missing = [
            name
            for name in FEEDBACK_FIELDS
            if state.feedback[name] is None
            and name not in state.unclear_fields
        ]

        if missing:
            field_name = missing[0]
            state.pending_field = field_name

            return ConversationReply(
                text=self._question_for(field_name, state.language),
                state=state.step,
            )

        # All fields are answered or explicitly marked unclear.
        state.terminal = True
        state.step = "completed"

        needs_followup = state.feedback["issue_resolved"] != "yes"

        if needs_followup:
            outcome = "needs_followup"
        elif state.unclear_fields:
            outcome = "completed_with_unclear_fields"
        else:
            outcome = "completed"

        result = self._result(state, outcome=outcome)
        result["needs_followup"] = needs_followup

        return ConversationReply(
            text={"en": "Thank you for sharing your feedback. We appreciate your time. Goodbye.", "hi": "अपनी प्रतिक्रिया साझा करने के लिए धन्यवाद। आपका समय देने के लिए आभार। नमस्कार।", "te": "మీ అభిప్రాయం తెలిపినందుకు ధన్యవాదాలు. మీ సమయానికి కృతజ్ఞతలు. నమస్కారం."}[state.language],
            action="hangup",
            state=state.step,
            result=result,
        )



    # ---------------------------------------------------------
    # Retry handling
    # ---------------------------------------------------------

    def _retry(
        self,
        state: ConversationState,
        field_name: str,
        question: str,
    ) -> ConversationReply:

        count = state.retries.get(field_name, 0) + 1
        state.retries[field_name] = count

        if count >= self.MAX_RETRIES:
            if field_name not in state.unclear_fields:
                state.unclear_fields.append(field_name)

            # Never ask service questions if identity was not confirmed.
            if field_name == "identity_confirmed":
                state.feedback["identity_confirmed"] = None
                state.terminal = True
                state.step = "identity_unconfirmed"

                result = self._result(
                    state,
                    outcome="identity_unconfirmed",
                )
                result["needs_followup"] = False

                return ConversationReply(
                    text=(
                        "I'm sorry, but I couldn't confirm the account "
                        "holder. I'll end the call here. Goodbye."
                    ),
                    action="hangup",
                    state=state.step,
                    result=result,
                )

            # Otherwise, move to the next unanswered field.
            next_field = next(
                (
                    name
                    for name in FEEDBACK_FIELDS
                    if state.feedback[name] is None
                    and name not in state.unclear_fields
                ),
                None,
            )

            if next_field is None:
                state.terminal = True
                state.step = "completed"

                result = self._result(
                    state,
                    outcome="completed_with_unclear_fields",
                )
                result["needs_followup"] = True

                return ConversationReply(
                    text=(
                        "Thank you for your time. We'll record the "
                        "information we could confirm. Goodbye."
                    ),
                    action="hangup",
                    state=state.step,
                    result=result,
                )

            question = self._question_for(next_field, state.language)

        return ConversationReply(
            text=question,
            state=state.step,
        )

    # ---------------------------------------------------------
    # Identity answer parsing
    # ---------------------------------------------------------

    @staticmethod
    def _parse_yes_no(text: str, language: str = "en") -> bool | None:
        normalized = text.lower().strip().replace("’", "'")
        if language == "hi":
            if normalized in {"हाँ", "हां", "जी", "जी हाँ", "haan", "han"}:
                return True
            if normalized in {"नहीं", "नही", "ना", "nahi", "nahin"}:
                return False
        elif language == "te":
            if normalized in {"అవును", "అవునండి", "అవును అండి"}:
                return True
            if normalized in {"కాదు", "కాదండి", "లేదు"}:
                return False
        normalized = re.sub(r"[.!?,;:]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        uncertain_patterns = (
            r"\bnot sure\b",
            r"\bunsure\b",
            r"\bi don't know\b",
            r"\bi do not know\b",
            r"\bmaybe\b",
            r"\bnot certain\b",
        )

        if any(
            re.search(pattern, normalized)
            for pattern in uncertain_patterns
        ):
            return None

        negative_patterns = (
            r"^no(?:pe)?\b",
            r"\bwrong person\b",
            r"\bnot me\b",
            r"\bnot the account holder\b",
            r"\bwrong number\b",
            r"^i am not\b",
            r"^i'm not\b",
        )
        positive_patterns = (
            r"^yes\b",
            r"^yeah\b",
            r"^yep\b",
            r"^correct\b",
            r"^that's right\b",
            r"^that is right\b",
            r"^that's me\b",
            r"^that is me\b",
            r"^speaking\b",
            r"\bi am the account holder\b",
            r"\bi'm the account holder\b",
            r"\bthis is the account holder\b",
            r"^(?:and\s+)?i(?:'m| am) (?:the )?(?:primary )?(?:account holder|subscriber|person responsible)\b",
            r"^(?:and\s+)?this is (?:the )?(?:primary )?(?:account holder|subscriber)\b",
        )
        is_negative = any(re.search(pattern, normalized) for pattern in negative_patterns)
        is_positive = any(re.search(pattern, normalized) for pattern in positive_patterns)
        if is_negative and is_positive:
            return None
        if is_negative:
            return False
        if is_positive:
            return True
        return None

    # ---------------------------------------------------------
    # Basic feedback extraction
    # ---------------------------------------------------------

    @staticmethod
    
    def _basic_extract(text: str) -> dict[str, Any]:
        """
        Deterministic parser for development and unit tests.

        Extracts known feedback fields without inventing unknown values.
        """
        t = text.lower().strip()
        found: dict[str, Any] = {}

        # 1. Issue resolution status.
        # Check negative statements first.
        if re.search(
            r"\b(not resolved|not restored|not fully restored|not working|still not working|not fixed|"
            r"issue is unresolved|problem is unresolved)\b",
            t,
        ):
            found["issue_resolved"] = "no"

        elif re.search(
            r"\b(partly|partially|somewhat resolved)\b",
            t,
        ):
            found["issue_resolved"] = "partly"

        elif re.search(
            r"\b(resolved|fixed|fully restored|restored|working fine|working now|"
            r"it's working now|it is working now|yes completely)\b",
            t,
        ):
            found["issue_resolved"] = "yes"

        # 2. Technician punctuality.
        # Check negative statements first.
        if re.search(
            r"\b(not on time|did not arrive on time|"
            r"didn't arrive on time|was not on time|"
            r"wasn't on time|arrived late|technician was late|"
            r"technician arrived late|delayed)\b",
            t,
        ):
            found["technician_on_time"] = False

        elif re.search(
            r"\b(on time|arrived on time|"
            r"technician was punctual|arrived as promised|"
            r"arrived during the promised time slot)\b",
            t,
        ):
            found["technician_on_time"] = True

        # 3. Technician rating.
        # Supports: "I will rate 5", "rating is 4", "rated 5".
        rating = re.search(
            r"\b(?:i\s+)?(?:will\s+)?(?:rate|rating|rated)"
            r"\s*(?:him|the technician)?\s*"
            r"(?:is|of|a)?\s*([1-5])(?!\s*\.\s*\d)\b",
            t,
        )

        if rating:
            found["technician_rating"] = int(rating.group(1))

        else:
            rating = re.search(
                r"\b([1-5])(?!\s*\.\s*\d)\s*(?:out of 5|/5)\b",
                t,
            )

            if rating:
                found["technician_rating"] = int(rating.group(1))

        # 4. Net Promoter Score (NPS).
        # Extract explicit NPS/recommendation scores.
        nps = re.search(
            r"\b(?:nps|recommend|recommendation|score)"
            r"\D{0,20}(\d{1,2})(?!\s*\.\s*\d)\b",
            t,
        )

        if nps:
            score = int(nps.group(1))
            if 0 <= score <= 10:
                found["nps"] = score

        # 5. Still-facing / ongoing internet problems.
        ongoing_problem_patterns = (
            r"\b(?:still facing|still having|still experiencing)\b",
            r"\b(?:internet|connection|network)\b.{0,35}"
            r"\b(?:dropping|disconnecting|disconnected|slow|unstable|"
            r"not working|keeps? disconnecting|cuts? out|buffering)\b",
            r"\b(?:keeps?|keep)\s+"
            r"(?:dropping|disconnecting|cutting out)\b",
            r"\b(?:drops?|disconnects?|cuts out)\s+"
            r"(?:frequently|often|every)\b",
        )

        no_problem_patterns = (
            r"\bnot facing (?:any )?(?:internet )?"
            r"(?:problems?|issues?)\b",
            r"\bno (?:internet |connection |network )?"
            r"(?:problems?|issues?)\b",
            r"\bno longer facing (?:any )?(?:internet )?"
            r"(?:problems?|issues?)\b",
            r"\b(?:don't|do not) have (?:any )?"
            r"(?:internet )?(?:problems?|issues?)\b",
            r"\b(?:internet|connection|network) is working fine\b",
            r"\b(?:all|everything) is working fine\b",
            r"\b(?:everything|all) is working(?: now| fine)?\b",
            r"\b(?:everything|all) works(?: fine)?\b",
            r"\b(?:the )?(?:problem|issue) (?:is )?resolved\b",
            r"\bno problems? (?:now|anymore)\b",
        )

        if any(
            re.search(pattern, t)
            for pattern in ongoing_problem_patterns
        ):
            # Preserve the customer's original words.
            found["still_facing"] = text.strip()

        elif any(
            re.search(pattern, t)
            for pattern in no_problem_patterns
        ):
            found["still_facing"] = "none"

        # 6. Other feedback.
        # Only identify explicit statements that there is no more feedback.
        if re.search(
            r"\b(no other feedback|nothing else|no comments|"
            r"nothing to add|no additional feedback)\b",
            t,
        ):
            found["other_feedback"] = "none"

        return found

    # ---------------------------------------------------------
    # Validation
    # ---------------------------------------------------------

    @staticmethod
    def _validated_fields(
        candidates: dict[str, Any],
    ) -> dict[str, Any]:

        valid: dict[str, Any] = {}

        resolved = candidates.get("issue_resolved")

        if (
            isinstance(resolved, str)
            and resolved in {"yes", "no", "partly"}
        ):
            valid["issue_resolved"] = resolved


        still_facing = candidates.get("still_facing")

        if isinstance(still_facing, str):
            value = still_facing.strip()

            if value:
                valid["still_facing"] = value[:1000]
            elif value == "":
                valid["still_facing"] = "none"

        on_time = candidates.get("technician_on_time")

        if isinstance(on_time, bool):
            valid["technician_on_time"] = on_time

        rating = candidates.get("technician_rating")

        if isinstance(rating, int) and not isinstance(rating, bool):
            if 1 <= rating <= 5:
                valid["technician_rating"] = rating

        nps = candidates.get("nps")

        if isinstance(nps, int) and not isinstance(nps, bool):
            if 0 <= nps <= 10:
                valid["nps"] = nps

        other = candidates.get("other_feedback")

        if isinstance(other, str):
            value = other.strip()

            if value:
                valid["other_feedback"] = value[:1000]

        return valid

    # ---------------------------------------------------------
    # Questions
    # ---------------------------------------------------------

    @staticmethod
    def _question_for(field_name: str, language: str = "en") -> str:
        translated = {
            "hi": {
                "identity_confirmed": "क्या आप खाताधारक हैं? कृपया हाँ या नहीं कहें।",
                "issue_resolved": "क्या आपकी इंटरनेट समस्या पूरी तरह हल हुई, आंशिक रूप से हल हुई, या हल नहीं हुई?",
                "still_facing": "क्या आपको अभी भी इंटरनेट की समस्या है? कृपया बताइए, या कहें कि अब कोई समस्या नहीं है।",
                "technician_on_time": "क्या तकनीशियन तय समय पर आए थे?",
                "technician_rating": "तकनीशियन को 1 से 5 तक कितनी रेटिंग देंगे?",
                "nps": "आप स्काईफ़ाइबर की सिफ़ारिश 0 से 10 तक कितनी करेंगे?",
                "other_feedback": "क्या आप कुछ और प्रतिक्रिया देना चाहेंगे?",
            },
            "te": {
                "identity_confirmed": "మీరు ఖాతాదారులా? దయచేసి అవును లేదా కాదు అని చెప్పండి.",
                "issue_resolved": "మీ ఇంటర్నెట్ సమస్య పూర్తిగా పరిష్కారమైందా, కొంతవరకు పరిష్కారమైందా, లేదా పరిష్కారం కాలేదా?",
                "still_facing": "మీకు ఇంకా ఇంటర్నెట్ సమస్యలు ఉన్నాయా? దయచేసి వివరించండి లేదా ఇప్పుడు సమస్యలు లేవని చెప్పండి.",
                "technician_on_time": "టెక్నీషియన్ వాగ్దానం చేసిన సమయానికి వచ్చారా?",
                "technician_rating": "టెక్నీషియన్‌కు 1 నుండి 5 వరకు ఎంత రేటింగ్ ఇస్తారు?",
                "nps": "స్కైఫైబర్‌ను 0 నుండి 10 వరకు ఎంతవరకు సిఫార్సు చేస్తారు?",
                "other_feedback": "ఇంకేమైనా అభిప్రాయం చెప్పాలనుకుంటున్నారా?",
            },
        }
        if language in translated:
            return translated[language][field_name]
        questions = {
            "identity_confirmed": (
                "Are you the account holder? Please answer yes or no."
            ),
            "issue_resolved": (
                "Has your internet issue been fully resolved, "
                "partly resolved, or not resolved?"
            ),
            "still_facing": (
                "Are you still facing any internet problems? "
                "Please describe them, or say no."
            ),
            "technician_on_time": (
                "Did the technician arrive during the promised time slot?"
            ),
            "technician_rating": (
                "On a scale of 1 to 5, how would you rate the technician?"
            ),
            "nps": (
                "On a scale of 0 to 10, how likely are you to "
                "recommend SkyFiber to others?"
            ),
            "other_feedback": (
                "Do you have any other feedback, or is there nothing "
                "else to add?"
            ),
        }

        return questions[field_name]

    # ---------------------------------------------------------
    # Caller intent detection
    # ---------------------------------------------------------

    @staticmethod
    def _is_opt_out(text: str) -> bool:
        t = text.lower()

        phrases = (
            "do not call",
            "don't call",
            "stop calling",
            "remove my number",
            "opt me out",
            "no more calls",
            "do not contact me",
            "don't contact me",
            "leave me alone",
            "fuck off",
            "f*** off",
            "asshole",
            "you idiot",
            "you moron",
        )

        return any(phrase in t for phrase in phrases)

    @staticmethod
    def _is_abusive_or_stop(text: str) -> bool:
        t = text.lower().strip()

        return t in {
            "stop",
            "hang up",
            "end the call",
            "bye",
            "bye bye",
            "goodbye",
            "that's all",
            "that is all",
            "i have to go",
            "gotta go",
            "shut up",
            "leave me alone",
        }

    @staticmethod
    def _is_callback(text: str) -> bool:
        t = text.lower()

        return any(
            phrase in t
            for phrase in (
                "call me later",
                "call back later",
                "call me tomorrow",
                "busy right now",
                "not a good time",
                "call again later",
            )
        )

    @staticmethod
    def _is_repeat(text: str) -> bool:
        normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
        return normalized in {
            "repeat", "say that again", "could you repeat that",
            "can you repeat that", "what was the question",
            "please repeat", "pardon", "come again",
        }

    @staticmethod
    def _repeat_question(field_name: str | None) -> str:
        alternatives = {
            "identity_confirmed": "Are you the account holder?",
            "issue_resolved": "Is your internet working now?",
            "still_facing": "Are you still having internet trouble?",
            "technician_on_time": "Did the technician arrive on time?",
            "technician_rating": "What score from 1 to 5 would you give the technician?",
            "nps": "How likely are you to recommend us, from 0 to 10?",
            "other_feedback": "Would you like to add anything else?",
        }
        if field_name in alternatives:
            return alternatives[field_name]
        return "What would you like me to repeat?"

    
    @staticmethod
    def _extract_callback_time(text: str) -> str | None:
        t = re.sub(r"\s+", " ", text.lower()).strip()

        # Preserve both the day and the requested time of day.
        match = re.search(
            r"\b(today|tomorrow)\s+"
            r"(morning|afternoon|evening|night)\b",
            t,
        )
        if match:
            return f"{match.group(1)} {match.group(2)}"

        # Handle common relative day and time expressions.
        if re.search(r"\btonight\b", t):
            return "tonight"

        if re.search(r"\btomorrow\b", t):
            return "tomorrow"

        if re.search(r"\btoday\b", t):
            return "today"

        match = re.search(
            r"\bthis\s+(morning|afternoon|evening)\b",
            t,
        )
        if match:
            return f"this {match.group(1)}"

        match = re.search(
            r"\b(morning|afternoon|evening)\b",
            t,
        )
        if match:
            return match.group(1)

        match = re.search(
            r"\b(?:at|after)\s+"
            r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
            t,
        )
        if match:
            return match.group(1).strip()

        return None


    # ---------------------------------------------------------
    # Result construction
    # ---------------------------------------------------------

    @staticmethod
    def _result(
        state: ConversationState,
        outcome: str,
    ) -> dict[str, Any]:

        result = state.feedback.copy()

        result.update(
            {
                "outcome": outcome,
                "unclear_fields": list(state.unclear_fields),
                "callback_time": state.callback_time,
                "do_not_call": outcome == "opted_out",
            }
        )

        return result
