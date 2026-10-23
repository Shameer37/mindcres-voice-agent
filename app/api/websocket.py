import asyncio
import base64
import hashlib
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

import numpy as np

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audio.buffer import AudioFrameBuffer
from app.audio.payload import decode_audio_payload
from app.audio.turn_detector import TurnDetector
from app.audio.utterance_buffer import UtteranceBuffer
from app.audio.vad import EnergyVAD, SustainedSpeechGate
from app.database import SessionLocal
from app.models import Call, TranscriptTurn, TurnMetric
from app.services.conversation_service import ConversationService
from app.services.stt_service import STTService
from app.services.tts_service import TTSService
from app.services.llm_service import LLMExtractionService
from app.services.llm_reply_service import LLMReplyService

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------
# Audio configuration
# ---------------------------------------------------------

VAD_THRESHOLD = float(os.getenv("VAD_RMS_THRESHOLD", "500"))
END_OF_TURN_SILENCE_MS = int(os.getenv("END_OF_TURN_SILENCE_MS", "800"))
MAX_UTTERANCE_SECONDS = 30.0
MIN_UTTERANCE_SECONDS = 0.30
FRAME_BYTES = 160

# Barge-in configuration
BARGE_IN_THRESHOLD_MS = int(os.getenv("BARGE_IN_MIN_SPEECH_MS", "280"))
BARGE_IN_MAX_GAP_MS = int(os.getenv("BARGE_IN_MAX_GAP_MS", "160"))

# No-input and call-duration watchdogs
INITIAL_RESPONSE_TIMEOUT_SEC = float(os.getenv("INITIAL_RESPONSE_TIMEOUT_SEC", "9"))
NO_INPUT_TIMEOUT_SEC = float(os.getenv("NO_INPUT_TIMEOUT_SEC", "7"))
REMINDER_GRACE_SEC = float(os.getenv("REMINDER_GRACE_SEC", "6"))
TIMEOUT_EDGE_GRACE_SEC = float(os.getenv("TIMEOUT_EDGE_GRACE_SEC", "0.40"))
MAX_CALL_DURATION_SEC = 240.0     # 4 minutes absolute limit
STT_TIMEOUT_SEC = float(os.getenv("STT_TIMEOUT_SEC", "25"))
TTS_TIMEOUT_SEC = float(os.getenv("TTS_TIMEOUT_SEC", "30"))
PLAYBACK_ACK_TIMEOUT_SEC = float(os.getenv("PLAYBACK_ACK_TIMEOUT_SEC", "20"))
ENABLE_LLM_REPLY_GENERATION = os.getenv("ENABLE_LLM_REPLY_GENERATION", "false").lower() in {"1", "true", "yes"}

# ---------------------------------------------------------
# Services
# ---------------------------------------------------------

stt_service = STTService(
    model_size=os.getenv("STT_MODEL_SIZE", "tiny"),
    device=os.getenv("STT_DEVICE", "cpu"),
    compute_type=os.getenv("STT_COMPUTE_TYPE", "int8"),
)

conversation_service = ConversationService()
llm_service = LLMExtractionService()
llm_reply_service = LLMReplyService()
tts_service = TTSService()


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def websocket_event_type(message: dict) -> str | None:
    """Read the assignment's `event` key, with legacy `type` fallback."""
    return message.get("event") or message.get("type")


def acknowledge_playback_mark(websocket: WebSocket, message: dict) -> bool:
    """Resolve only the currently pending mark named by the client."""
    mark_name = message.get("name")
    if mark_name is None and isinstance(message.get("mark"), dict):
        mark_name = message["mark"].get("name")
    pending = getattr(websocket, "_skyfiber_mark_ack_events", {})
    event = pending.pop(mark_name, None)
    if event is None:
        return False
    websocket._skyfiber_last_acknowledged_mark = mark_name
    event.set()
    return True


def playback_audio_is_active(websocket: WebSocket, task: asyncio.Task | None) -> bool:
    """Treat a mark-acknowledged task as finished for VAD/barge-in gating."""
    if task is None or task.done():
        return False
    mark_name = getattr(task, "_skyfiber_mark_ack_name", None)
    return mark_name is None or getattr(
        websocket, "_skyfiber_last_acknowledged_mark", None
    ) != mark_name


def no_input_action(
    silence_sec: float,
    response_timeout_sec: float,
    reminder_sent: bool,
    reminder_grace_sec: float = REMINDER_GRACE_SEC,
) -> str | None:
    """Return the single reminder or final action for an unanswered turn."""
    if reminder_sent:
        return "hangup" if silence_sec >= reminder_grace_sec + TIMEOUT_EDGE_GRACE_SEC else None
    return "remind" if silence_sec >= response_timeout_sec + TIMEOUT_EDGE_GRACE_SEC else None


def no_input_watchdog_ready(
    conversation_started: bool,
    question_asked_at: float | None,
    playback_task: asyncio.Task | None,
    utterance_active: bool,
) -> bool:
    """Only measure silence while a prompt is complete and input is idle."""
    return (
        conversation_started
        and question_asked_at is not None
        and playback_task is None
        and not utterance_active
    )


def arm_question_timer(conversation_started: bool, now: float) -> float | None:
    """Arm the response clock only after a conversation prompt completes."""
    return now if conversation_started else None


async def finish_playback_while_receiving(
    websocket: WebSocket,
    playback_task: asyncio.Task | None,
    call_id: str,
) -> bool:
    """Drain terminal audio while keeping the socket receive loop alive.

    Waiting on playback_task directly deadlocks: that task waits for a client
    mark, but only this handler can receive and acknowledge that mark.
    """
    if playback_task is None:
        return True
    while not playback_task.done():
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            playback_task.cancel()
            await asyncio.gather(playback_task, return_exceptions=True)
            logger.info("Caller disconnected during terminal TTS for %s", call_id)
            return False

        if websocket_event_type(message) == "mark":
            if acknowledge_playback_mark(websocket, message):
                await asyncio.sleep(0)

    # _play_reply consumes TTS errors, but gather also makes this helper safe
    # if a future implementation lets a playback exception escape.
    await asyncio.gather(playback_task, return_exceptions=True)
    return True


async def send_terminal_event(
    websocket: WebSocket,
    call_id: str,
    reason: str,
) -> bool:
    """Send the terminal protocol event before closing, tolerating disconnects."""
    try:
        await websocket.send_json({
            "event": "hangup",
            "type": "hangup",
            "call_id": call_id,
            "reason": reason,
        })
    except Exception:
        logger.info("Unable to send terminal event for disconnected call %s", call_id)
        try:
            await websocket.close()
        except Exception:
            pass
        return False
    try:
        await websocket.close()
    except Exception:
        logger.debug("Socket already closed after terminal event for %s", call_id)
    return True


def conversation_service_fields() -> tuple[str, ...]:
    """Return the feedback fields in conversation order."""
    return (
        "issue_resolved",
        "still_facing",
        "technician_on_time",
        "technician_rating",
        "nps",
        "other_feedback",
    )



def get_expected_field(state) -> str | None:
    """Return the field currently expected by the conversation."""

    # Keep the identity verification step first.
    if state.step == "identity":
        return "identity_confirmed"

    # Honor the field the conversation explicitly asked the caller to answer.
    pending = getattr(state, "pending_field", None)

    if (
        pending
        and pending in conversation_service_fields()
        and state.feedback.get(pending) is None
        and pending not in state.unclear_fields
    ):
        return pending

    # Otherwise, select the next unanswered feedback field.
    for field_name in conversation_service_fields():
        if (
            state.feedback.get(field_name) is None
            and field_name not in state.unclear_fields
        ):
            return field_name

    return None



# ---------------------------------------------------------
# Fast deterministic extraction
# ---------------------------------------------------------

def fast_extract_answer(
    user_text: str,
    expected_field: str | None,
    language: str = "en",
) -> dict | None:
    """Extract clear answers deterministically without an LLM request."""

    text = user_text.strip()

    normalized = re.sub(r"[.!?,;:]+", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Do not let the numeric fallback parse only the integer prefix of a
    # decimal (for example, taking 1 from "1.3").
    decimal_score = re.search(
        r"(?<!\w)\d+\s*\.\s*\d+(?!\w)|"
        r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)"
        r"\s+point\s+(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
        text.lower(),
    )
    if decimal_score:
        return None

    if expected_field == "other_feedback" and _is_feedback_intent(normalized):
        # It is an intent to add feedback, not the feedback itself. This
        # sentinel suppresses LLM extraction; the state handler asks for it.
        return {"other_feedback": None}

    yes_no = ConversationService._parse_yes_no(text, language)
    basic_answer = ConversationService._basic_extract(text)

    # The deterministic parser already handles unambiguous full-sentence
    # answers. Avoid a slow model request when the expected field is clear.
    if expected_field in basic_answer:
        return {expected_field: basic_answer[expected_field]}

    if expected_field == "identity_confirmed":
        if yes_no is not None:
            return {"identity_confirmed": yes_no}
        return None

    if language in {"hi", "te"}:
        if expected_field == "issue_resolved":
            if yes_no is True:
                return {"issue_resolved": "yes"}
            if yes_no is False:
                return {"issue_resolved": "no"}
            normalized_local = text.lower().strip()
            partly = {"आंशिक", "थोड़ा", "కొంతవరకు", "పాక్షికంగా"}
            if normalized_local in partly:
                return {"issue_resolved": "partly"}
        if expected_field == "technician_on_time" and yes_no is not None:
            return {"technician_on_time": yes_no}
        if expected_field == "still_facing" and yes_no is False:
            return {"still_facing": "none"}
        if expected_field == "still_facing" and len(text.split()) >= 3:
            # This prompt explicitly asks for a problem description. Preserve
            # the caller's transcript verbatim as a pending-issue answer.
            return {"still_facing": text.strip()}
        if expected_field == "other_feedback" and yes_no is False:
            return {"other_feedback": "none"}
        if expected_field == "other_feedback" and len(text.split()) >= 2:
            return {"other_feedback": text.strip()}

        localized_scores = {
            "hi": {"शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10},
            "te": {"సున్నా": 0, "ఒకటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5, "ఆరు": 6, "ఏడు": 7, "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10},
        }
        if expected_field in {"technician_rating", "nps"}:
            maximum = 5 if expected_field == "technician_rating" else 10
            token = text.strip().lower()
            if token in localized_scores[language] and localized_scores[language][token] <= maximum:
                return {expected_field: localized_scores[language][token]}
            if token.isdigit() and 0 <= int(token) <= maximum:
                return {expected_field: int(token)}
        return None

    if expected_field == "technician_on_time":
        if yes_no is not None:
            return {"technician_on_time": yes_no}
        return None

    # Technician rating: valid range is 1–5.
    if expected_field == "technician_rating":
        match = re.fullmatch(
            r"([1-5])(?:\s*/\s*5)?",
            normalized,
        )
        if match:
            return {"technician_rating": int(match.group(1))}

        patterns = [
            r"(?:i\s+will\s+rate|i\s+would\s+rate|"
            r"i(?:'d)?\s+rate|rate\s+it|rating\s+is|"
            r"give\s+it|i\s+give\s+it)\s+(?:a\s+)?([1-5])",
            r"([1-5])\s+out\s+of\s+5",
            r"(?:i\s+would\s+give|i(?:'d)?\s+give|"
            r"i\s+will\s+give)\s+(?:it\s+)?([1-5])\s+out\s+of\s+5",
        ]

        for pattern in patterns:
            match = re.fullmatch(pattern, normalized)
            if match:
                return {
                    "technician_rating": int(match.group(1))
                }

        score = _extract_spoken_score(normalized, maximum=5)
        if score is not None:
            return {"technician_rating": score}

        return None

    # NPS: valid range is 0–10.
    if expected_field == "nps":
        match = re.fullmatch(r"(10|[0-9])", normalized)
        if match:
            return {"nps": int(match.group(1))}

        patterns = [
            r"(?:i\s+would\s+give|i(?:'d)?\s+give|"
            r"i\s+will\s+give|my\s+score\s+is|"
            r"score\s+is|i\s+rate|rating\s+is)"
            r"\s+(10|[0-9])",
            r"(10|[0-9])\s+out\s+of\s+10",
            r"(?:i\s+would\s+give|i(?:'d)?\s+give|"
            r"i\s+will\s+give)\s+(10|[0-9])\s+out\s+of\s+10",
        ]

        for pattern in patterns:
            match = re.fullmatch(pattern, normalized)
            if match:
                return {"nps": int(match.group(1))}

        score = _extract_spoken_score(normalized, maximum=10)
        if score is not None:
            return {"nps": score}

        return None

    if expected_field == "issue_resolved":
        if normalized in {"fully restored", "restored", "it's working now", "it is working now", "yes completely", "yes fully", "yes, completely"}:
            return {"issue_resolved": "yes"}

        if normalized in {"partly resolved", "partially resolved", "partly fixed", "somewhat resolved"}:
            return {"issue_resolved": "partly"}

        if normalized in {"still not working", "not resolved", "not fixed", "not working", "still broken"}:
            return {"issue_resolved": "no"}

        if normalized in {
            "yes",
            "yeah",
            "yep",
            "correct",
            "that's right",
            "that is right",
            "resolved",
            "fully resolved",
        }:
            return {"issue_resolved": "yes"}

        if normalized in {
            "no",
            "nope",
            "not resolved",
            "not fixed",
        }:
            return {"issue_resolved": "no"}

        if normalized in {
            "partly",
            "partially",
            "somewhat",
        }:
            return {"issue_resolved": "partly"}

        if re.search(r"\b(not|isn't|wasn't|hasn't|still)\b", normalized) and re.search(
            r"\b(resolv\w*|fix\w*|work\w*)\b", normalized
        ):
            return {"issue_resolved": "no"}

        return None

    if expected_field == "still_facing":
        if normalized in {
            "no",
            "nope",
            "nah",
            "none",
            "not anymore",
            "no problems",
            "no issues",
            "no problems",
            "no problem",
            "everything is working",
            "everything is working now",
            "everything works",
        }:
            return {"still_facing": "none"}

        if re.search(r"\b(?:still )?(?:disconnecting|dropping)\b", normalized):
            return {"still_facing": text}

        if len(normalized.split()) >= 3 and re.search(
            r"\b(internet|connection|wifi|wi-fi|router|service|disconnect|dropp|slow|outage)\w*\b",
            normalized,
        ):
            return {"still_facing": text}

        return None

    if expected_field == "other_feedback":
        if normalized in {
            "no",
            "nope",
            "none",
            "nothing else",
            "no other feedback",
            "no additional feedback",
            "nothing to add",
            "no comments",
        }:
            return {"other_feedback": "none"}

        return None

    return None


def _is_feedback_intent(normalized: str) -> bool:
    """True when the caller offers more feedback but has not stated it."""
    return bool(re.fullmatch(
        r"(?:"
        r"(?:i have|i've got|there is|there's)\s+(?:another|more|some)\s+(?:feedback|comments?)"
        r"|something else to add"
        r"|(?:another|more)\s+(?:feedback|comments?)"
        r")",
        normalized,
    ))


_SPOKEN_DIGITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _extract_spoken_score(text: str, *, maximum: int) -> int | None:
    """Parse a single digit or spoken number used as a requested score."""
    tokens = re.findall(
        r"\b(10|[0-9]|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        text,
    )
    for token in tokens:
        score = int(token) if token.isdigit() else _SPOKEN_DIGITS[token]
        if 0 <= score <= maximum:
            return score
    return None


# ---------------------------------------------------------
# Extraction validation and merge
# ---------------------------------------------------------

def merge_extractions(
    user_text: str,
    llm_candidates: dict | None,
    *,
    identity_context: bool = False,
) -> dict | None:
    """Merge validated LLM extraction with deterministic fallback."""

    fallback = conversation_service._basic_extract(user_text) or {}
    candidates = dict(fallback)

    field_names = {
        "issue_resolved",
        "still_facing",
        "technician_on_time",
        "technician_rating",
        "nps",
        "other_feedback",
    }

    llm_available = (
        isinstance(llm_candidates, dict)
        and any(key in llm_candidates for key in field_names)
    )

    if llm_available:
        for key in field_names:
            if key not in llm_candidates:
                continue

            value = llm_candidates[key]

            if value is None:
                candidates.pop(key, None)
                continue

            if key == "issue_resolved":
                valid = (
                    isinstance(value, str)
                    and value in {"yes", "no", "partly"}
                )

            elif key == "still_facing":
                valid = (
                    isinstance(value, str)
                    and bool(value.strip())
                    and len(value) <= 1000
                )

            elif key == "technician_on_time":
                valid = isinstance(value, bool)

            elif key == "technician_rating":
                valid = (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 1 <= value <= 5
                )

            elif key == "nps":
                valid = (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 10
                )

            elif key == "other_feedback":
                valid = (
                    isinstance(value, str)
                    and bool(value.strip())
                    and len(value) <= 1000
                )

            else:
                valid = False

            if valid:
                candidates[key] = value

    # Identity is determined only from the caller's utterance.
    if identity_context:
        identity = ConversationService._parse_yes_no(user_text)

        if identity is None:
            candidates.pop("identity_confirmed", None)
        else:
            candidates["identity_confirmed"] = identity
    else:
        candidates.pop("identity_confirmed", None)

    return candidates or None


async def extract_turn_candidates(
    user_text: str,
    state,
    expected_field: str | None,
    extractor,
) -> tuple[dict | None, bool, float, str, str | None]:
    """Use clear question-aware rules first and return extraction trace metadata."""
    started = time.perf_counter()
    fast_answer = fast_extract_answer(
        user_text=user_text,
        expected_field=expected_field,
        language=state.language,
    )
    llm_attempted = False
    fallback_reason: str | None = None

    if state.step == "identity":
        llm_candidates = fast_answer
        parser = "identity_rules"
    elif fast_answer is not None:
        llm_candidates = fast_answer
        parser = "question_rules"
    elif expected_field in {"technician_rating", "nps"} and (
        len(user_text.split()) <= 2
        or re.search(r"(?<!\w)\d+\s*\.\s*\d+(?!\w)", user_text)
        or re.search(
            r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)"
            r"\s+point\s+(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b",
            user_text.lower(),
        )
    ):
        llm_candidates = None
        parser = "numeric_validation"
        fallback_reason = "numeric_answer_invalid_or_decimal"
    else:
        llm_attempted = True
        parser = "ollama_extraction"
        fallback_reason = "question_rules_found_no_confident_answer"
        normalized = " ".join(user_text.replace("’", "'").lower().split())
        try:
            llm_candidates = await extractor.extract(
                user_text=user_text,
                current_feedback=state.feedback,
                expected_field=expected_field,
                language=state.language,
            )
        except Exception:
            logger.exception(
                "LLM extraction failed for call_id=%s; using fallback",
                state.call_id,
            )
            llm_candidates = None
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Transcript logging is DEBUG-only because free-text feedback can
        # contain personal information. It includes both source and normalized
        # forms to distinguish STT output from answer-parser behavior.
        logger.debug(
            "LLM extraction fallback: call_id=%s expected=%s reason=%s parser=%s "
            "original_transcript=%r normalized_transcript=%r elapsed_ms=%.1f",
            state.call_id, expected_field, fallback_reason, parser,
            user_text, normalized, elapsed_ms,
        )

    extracted = merge_extractions(
        user_text=user_text,
        llm_candidates=llm_candidates,
        identity_context=(state.step == "identity"),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return extracted, llm_attempted, elapsed_ms, parser, fallback_reason


# ---------------------------------------------------------
# Transcript persistence and timing metrics
# ---------------------------------------------------------

async def save_and_send_transcript(
    websocket: WebSocket,
    db: Session,
    call_id: str,
    role: str,
    text: str,
    stt_ms: float | None = None,
) -> int | None:
    """Save and send a transcript, returning its turn number."""

    cleaned_text = text.strip()

    if not cleaned_text:
        return None

    turn_number = (
        db.query(TranscriptTurn)
        .filter(TranscriptTurn.call_id == call_id)
        .count()
        + 1
    )

    transcript_turn = TranscriptTurn(
        call_id=call_id,
        speaker=role,
        text=cleaned_text,
    )

    db.add(transcript_turn)
    db.flush()

    if role == "caller" and stt_ms is not None:
        db.add(
            TurnMetric(
                call_id=call_id,
                turn_number=turn_number,
                stt_ms=stt_ms,
            )
        )

    db.commit()

    await websocket.send_json(
        {
            "type": "transcript",
            "role": role,
            "text": cleaned_text,
            "final": True,
            "turn_number": turn_number,
        }
    )

    return turn_number


def update_turn_metric(
    db: Session,
    call_id: str,
    turn_number: int,
    **values,
) -> None:
    """Update timing metrics for an existing caller turn."""

    try:
        metric = db.scalar(
            select(TurnMetric).where(
                TurnMetric.call_id == call_id,
                TurnMetric.turn_number == turn_number,
            )
        )

        if metric is None:
            logger.warning(
                "Turn metric not found: call_id=%s turn=%s",
                call_id,
                turn_number,
            )
            return

        allowed_fields = {
            "stt_ms",
            "llm_ms",
            "tts_first_audio_ms",
            "response_latency_ms",
            "interrupted_at_ms",
        }

        for field, value in values.items():
            if field in allowed_fields:
                setattr(metric, field, value)

        db.commit()

    except Exception:
        db.rollback()
        logger.exception(
            "Failed to update metrics for call_id=%s turn=%s",
            call_id,
            turn_number,
        )


# ---------------------------------------------------------
# TTS and bot response
# ---------------------------------------------------------

async def save_and_send_bot_reply(
    websocket: WebSocket,
    db: Session,
    call_id: str,
    text: str,
    turn_number: int | None = None,
    response_started_at: float | None = None,
) -> asyncio.Task | None:
    """Persist and publish a bot reply, then play audio in the background."""

    cleaned_text = text.strip()

    if not cleaned_text:
        return None

    try:
        db.add(
            TranscriptTurn(
                call_id=call_id,
                speaker="bot",
                text=cleaned_text,
            )
        )
        db.commit()

    except Exception:
        db.rollback()
        raise

    await websocket.send_json(
        {
            "event": "transcript",
            "type": "transcript",
            "role": "bot",
            "text": cleaned_text,
            "final": True,
        }
    )

    mark_ack_event = asyncio.Event()
    mark_ack_name = uuid.uuid4().hex
    mark_ack_events = getattr(websocket, "_skyfiber_mark_ack_events", None)
    if mark_ack_events is not None:
        mark_ack_events[mark_ack_name] = mark_ack_event

    async def _play_reply() -> None:
        try:
            await asyncio.wait_for(
                send_tts_audio(
                    websocket=websocket,
                    text=cleaned_text,
                    db=db,
                    call_id=call_id,
                    turn_number=turn_number,
                    response_started_at=response_started_at,
                    mark_ack_event=mark_ack_event,
                    mark_ack_name=mark_ack_name,
                ),
                timeout=TTS_TIMEOUT_SEC,
            )
        except asyncio.CancelledError:
            logger.info("TTS playback cancelled for call %s", call_id)
            raise
        except Exception as exc:
            logger.warning(
                "TTS request failed call_id=%s turn=%s stage=synthesize_or_stream error_type=%s",
                call_id, turn_number, type(exc).__name__,
            )
            try:
                await websocket.send_json(
                    {
                        "event": "error",
                        "type": "error",
                        "message": "Speech generation failed. Please try again.",
                    }
                )
            except Exception:
                logger.debug(
                    "Unable to send TTS failure event for %s",
                    call_id,
                )
        finally:
            if mark_ack_events is not None:
                mark_ack_events.pop(mark_ack_name, None)

    task = asyncio.create_task(
        _play_reply(),
        name=f"tts-{call_id}",
    )
    task._skyfiber_mark_ack_name = mark_ack_name
    return task


async def send_tts_audio(
    websocket: WebSocket,
    text: str,
    db: Session | None = None,
    call_id: str | None = None,
    turn_number: int | None = None,
    response_started_at: float | None = None,
    mark_ack_event: asyncio.Event | None = None,
    mark_ack_name: str | None = None,
) -> None:
    """Send paced 20 ms G.711 μ-law frames and record first-audio latency."""

    tts_started = time.perf_counter()

    language = getattr(websocket, "_skyfiber_language", "en")
    synthesis_started = time.perf_counter()
    frames = await tts_service.synthesize_frames(text, language=language)
    synthesis_ms = (time.perf_counter() - synthesis_started) * 1000

    if not frames:
        raise RuntimeError("TTS produced no audio frames; provider did not return speech.")

    if any(len(frame) != FRAME_BYTES for frame in frames):
        raise ValueError("TTS returned a frame that is not exactly 160 bytes.")

    playback_start_perf = time.perf_counter()
    playback_start = time.monotonic()
    first_frame_delivery_ms = None
    first_frame_sent = False
    maximum_send_lag_ms = 0.0

    for index, frame in enumerate(frames):
        target_time = playback_start + (index * 0.02)
        delay = target_time - time.monotonic()

        if delay > 0:
            await asyncio.sleep(delay)
        else:
            maximum_send_lag_ms = max(maximum_send_lag_ms, -delay * 1000)

        await websocket.send_json(
            {
                "event": "media",
                "type": "media",
                "media": {
                    "payload": base64.b64encode(frame).decode("ascii"),
                },
            }
        )

        if not first_frame_sent:
            first_frame_sent = True
            first_audio_at = time.perf_counter()
            first_frame_delivery_ms = (first_audio_at - playback_start_perf) * 1000

            if (
                db is not None
                and call_id is not None
                and turn_number is not None
            ):
                metrics = {
                    "tts_first_audio_ms": (
                        first_audio_at - tts_started
                    ) * 1000,
                }

                if response_started_at is not None:
                    metrics["response_latency_ms"] = (
                        first_audio_at - response_started_at
                    ) * 1000

                update_turn_metric(
                    db=db,
                    call_id=call_id,
                    turn_number=turn_number,
                    **metrics,
                )

    logger.info(
        "TTS stream sent: call_id=%s frames=%d synthesis_ms=%.1f first_frame_delivery_ms=%.1f audio_duration_ms=%d max_send_lag_ms=%.1f",
        call_id,
        len(frames),
        synthesis_ms,
        first_frame_delivery_ms or 0.0,
        len(frames) * 20,
        maximum_send_lag_ms,
    )

    await websocket.send_json(
        {
            "event": "mark",
            "type": "mark",
            "mark": {
                "name": mark_ack_name or "bot_audio_complete",
            },
        }
    )
    # A terminal response must not close the socket while the browser is still
    # draining queued audio and acknowledging its mark. The client echoes the
    # mark name; the receive loop signals this event.
    if mark_ack_event is not None:
        playback_ack_started = time.perf_counter()
        await asyncio.wait_for(
            mark_ack_event.wait(), timeout=PLAYBACK_ACK_TIMEOUT_SEC
        )
        logger.info(
            "TTS playback completion acknowledged call_id=%s ack_wait_ms=%.1f total_ms=%.1f",
            call_id, (time.perf_counter() - playback_ack_started) * 1000,
            (time.perf_counter() - tts_started) * 1000,
        )


# ---------------------------------------------------------
# Call completion helpers
# ---------------------------------------------------------

def finish_call(
    db: Session,
    call: Call,
    status: str,
    reason: str,
    result: dict | None = None,
) -> None:
    """Persist the final call state and result."""

    # Several cleanup paths can converge (for example TTS failure followed by
    # disconnect). The first committed terminal result is authoritative.
    if call.status in {"completed", "failed", "opted_out"} and call.ended_at is not None:
        return

    call.status = status
    call.end_reason = reason
    call.ended_at = utc_now()

    if result is not None:
        from app.schemas import CallResult, FeedbackResult

        normalized = dict(result)
        normalized["end_reason"] = reason
        normalized["do_not_call"] = (
            status == "opted_out"
            or bool(normalized.get("do_not_call", False))
        )
        validated = FeedbackResult.model_validate(normalized).model_dump()
        if validated["outcome"] == "no_response" and any(
            value is not None
            for value in (
                validated["identity_confirmed"],
                validated["issue_resolved"],
                validated["still_facing"],
                validated["technician_on_time"],
                validated["technician_rating"],
                validated["nps"],
                validated["other_feedback"],
            )
        ):
            # Enforce the schema/business invariant at the persistence edge,
            # so every terminal path preserves partial caller participation.
            validated["outcome"] = "partial"
            if (
                validated["issue_resolved"] in {"no", "partly"}
                or (
                    validated["still_facing"] is not None
                    and validated["still_facing"].strip().lower() != "none"
                )
            ):
                validated["needs_followup"] = True
        outcome_aliases = {
            "needs_followup": "completed",
            "completed_with_unclear_fields": "completed",
            "caller_requested_end": "partial",
            "identity_unconfirmed": "error",
            "disconnected": "partial",
            "caller_hangup": "partial",
        }
        outcome = outcome_aliases.get(validated["outcome"], validated["outcome"])
        started_at = call.started_at
        ended_at = call.ended_at
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if ended_at is not None and ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=timezone.utc)
        duration = (
            max(0.0, (ended_at - started_at).total_seconds())
            if started_at is not None and ended_at is not None
            else None
        )
        call_result = CallResult.model_validate(
            {
                "call_id": call.id,
                "customer_id": call.customer_id,
                "outcome": outcome,
                "end_reason": reason,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_sec": round(duration, 3) if duration is not None else None,
                "answers": validated,
                "needs_followup": validated["needs_followup"],
                "do_not_call": validated["do_not_call"],
                "callback_time": validated["callback_time"],
                "unclear_fields": validated["unclear_fields"],
            }
        )
        call.result = call_result.model_dump(mode="json")
        call.outcome = outcome
        call.needs_followup = validated["needs_followup"]

    if status == "opted_out":
        call.do_not_call = True

    db.commit()


def get_partial_result(
    call_id: str,
    outcome: str,
) -> dict | None:
    """Build a partial result from in-memory conversation state."""

    try:
        state = conversation_service.get_state(call_id)
    except ValueError:
        state = None

    if state is None:
        return {
            "identity_confirmed": None,
            "issue_resolved": None,
            "still_facing": None,
            "technician_on_time": None,
            "technician_rating": None,
            "nps": None,
            "other_feedback": None,
            "outcome": outcome,
            "needs_followup": outcome != "no_response",
            "unclear_fields": [],
            "callback_time": None,
        }

    # A later timeout does not erase a caller's earlier valid answers. Keep
    # `no_response` reserved for calls where no structured answer was saved.
    has_response = any(value is not None for value in state.feedback.values())
    partial_outcome = "partial" if outcome == "no_response" and has_response else outcome
    needs_followup = (
        state.feedback.get("issue_resolved") in {"no", "partly"}
        or (
            isinstance(state.feedback.get("still_facing"), str)
            and state.feedback["still_facing"].strip().lower() not in {"", "none"}
        )
    )
    return {
        **state.feedback,
        "outcome": partial_outcome,
        "needs_followup": needs_followup,
        "unclear_fields": list(state.unclear_fields),
        "callback_time": state.callback_time,
    }
    
    # ---------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------

@router.websocket("/ws/call/{call_id}")
async def call_websocket(
    websocket: WebSocket,
    call_id: str,
):
    await websocket.accept()
    db: Session = SessionLocal()

    # Background TTS playback state.
    playback_task: asyncio.Task | None = None
    websocket._skyfiber_mark_ack_events = {}
    playback_turn_number: int | None = None
    playback_started_at: float | None = None
    barge_in_gate = SustainedSpeechGate(
        min_speech_ms=BARGE_IN_THRESHOLD_MS,
        max_gap_ms=BARGE_IN_MAX_GAP_MS,
    )
    stt_failure_count = 0

    try:
        from sqlalchemy.orm import joinedload
        call = db.scalar(
            select(Call)
            .options(joinedload(Call.customer))
            .where(Call.id == call_id)
        )

        if call is None:
            await websocket.send_json(
                {
                    "event": "error",
                    "type": "error",
                    "message": "Call not found.",
                }
            )
            await websocket.close(code=1008)
            return

        if call.status in {"completed", "failed", "opted_out"}:
            await websocket.send_json(
                {
                    "event": "error",
                    "type": "error",
                    "message": f"Call is already {call.status}.",
                }
            )
            await websocket.close(code=1008)
            return

        call.status = "in_progress"
        websocket._skyfiber_language = (
            call.customer.preferred_language if call.customer else "en"
        )

        if call.started_at is None:
            call.started_at = utc_now()

        db.commit()

        # Initialize audio pipeline.
        frame_buffer = AudioFrameBuffer(frame_size=FRAME_BYTES)

        vad = EnergyVAD(
            threshold=VAD_THRESHOLD,
        )

        turn_detector = TurnDetector(
            silence_duration_ms=END_OF_TURN_SILENCE_MS,
            frame_duration_ms=20,
        )

        utterance_buffer = UtteranceBuffer(
            max_duration_seconds=MAX_UTTERANCE_SECONDS,
            sample_rate=8000,
        )

        conversation_started = False
        utterance_active = False

        # Watchdog timers
        bot_speaking = False          # True while TTS audio is being sent
        last_speech_time: float = time.monotonic()   # last time caller spoke
        call_start_time: float = time.monotonic()    # absolute call start
        no_input_reprompated = False  # True after first re-prompt sent
        question_asked_at: float | None = None       # when last bot Q was asked
        awaiting_first_response = True

        await websocket.send_json(
            {
                "event": "ready",
                "type": "ready",
                "call_id": call_id,
                "message": "WebSocket connected and ready.",
            }
        )
        last_vad_speech = False
        media_stats_started = time.monotonic()
        media_payloads = media_bytes = media_frames = media_bad_sizes = media_gaps = 0
        media_previous_at: float | None = None
        while True:
            # --------------------------------------------------
            # Watchdog checks (run each iteration, with timeout)
            # --------------------------------------------------
            now = time.monotonic()

            if playback_task is not None and playback_task.done():
                playback_task = None
                playback_turn_number = None
                playback_started_at = None
                barge_in_gate.reset()
                # Silence timing starts after queued agent speech has
                # finished, not while the caller is hearing the question.
                question_asked_at = arm_question_timer(conversation_started, now)
                if question_asked_at is not None:
                    state_name = (
                        conversation_service.get_state(call_id).step
                        if conversation_started
                        else "not_started"
                    )
                    logger.warning(
                        "Response timer armed at=%.3f call_id=%s turn=%s state=%s initial=%s timeout_sec=%.2f mark=%s",
                        time.time(),
                        call_id,
                        conversation_service.get_state(call_id).turn_number,
                        state_name,
                        awaiting_first_response,
                        INITIAL_RESPONSE_TIMEOUT_SEC if awaiting_first_response else NO_INPUT_TIMEOUT_SEC,
                        getattr(websocket, "_skyfiber_last_acknowledged_mark", None),
                    )

            # 4-minute max call duration.
            if now - call_start_time >= MAX_CALL_DURATION_SEC:
                partial = get_partial_result(call_id, outcome="partial")
                finish_call(db=db, call=call, status="completed",
                            reason="max_duration", result=partial)
                playback_task = await save_and_send_bot_reply(
                    websocket=websocket, db=db, call_id=call_id,
                    text="I'm sorry, we've reached the maximum call time. "
                         "Thank you for your feedback. Goodbye.",
                )
                if not await finish_playback_while_receiving(
                    websocket, playback_task, call_id
                ):
                    return
                await send_terminal_event(websocket, call_id, "max_duration")
                return

            # No-input watchdog (only when conversation is active and bot has asked a Q).
            if no_input_watchdog_ready(
                conversation_started, question_asked_at,
                playback_task, utterance_active,
            ):
                response_timeout = (
                    INITIAL_RESPONSE_TIMEOUT_SEC
                    if awaiting_first_response
                    else NO_INPUT_TIMEOUT_SEC
                )
                silence_sec = now - max(last_speech_time, question_asked_at)
                action = no_input_action(
                silence_sec, response_timeout, no_input_reprompated,
                REMINDER_GRACE_SEC,
                )
                if action is not None:
                    state = conversation_service.get_state(call_id)
                    logger.warning(
                        "Response timer fired at=%.3f call_id=%s turn=%s state=%s action=%s elapsed_sec=%.2f reminder_sent=%s",
                        time.time(),
                        call_id,
                        state.turn_number,
                        state.step,
                        action,
                        silence_sec,
                        no_input_reprompated,
                    )
                if action == "hangup":
                    # Second timeout: hang up.
                    partial = get_partial_result(call_id, outcome="no_response")
                    finish_call(db=db, call=call, status="completed",
                                reason="no_input", result=partial)
                    playback_task = await save_and_send_bot_reply(
                        websocket=websocket, db=db, call_id=call_id,
                        text="I haven't heard anything. Thank you for your time. Goodbye.",
                    )
                    if not await finish_playback_while_receiving(
                        websocket, playback_task, call_id
                    ):
                        return
                    await send_terminal_event(websocket, call_id, "no_input")
                    return
                elif action == "remind":
                    # First timeout: re-prompt once.
                    no_input_reprompated = True
                    # Keep the timer unarmed while the reminder is generated
                    # and played; its playback completion starts the grace period.
                    question_asked_at = None
                    playback_task = await save_and_send_bot_reply(
                        websocket=websocket, db=db, call_id=call_id,
                        text="Are you still there? Please go ahead.",
                    )
                    playback_turn_number = None
                    playback_started_at = time.perf_counter()

            # Receive next message (with short timeout to allow watchdog).
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            # The assignment's protocol uses `event`; accept legacy `type`
            # messages as well for existing simulator clients.
            message_type = websocket_event_type(message)
            # ---------------------------------------------
            # Start conversation
            # ---------------------------------------------

            if message_type == "start":
                if not conversation_started:
                    greeting = conversation_service.start(
                        call_id=call_id,
                        customer_id=call.customer_id,
                        language=call.customer.preferred_language if call.customer else "en",
                    )

                    playback_task = await save_and_send_bot_reply(
                        websocket=websocket,
                        db=db,
                        call_id=call_id,
                        text=greeting.text,
                    )

                    playback_turn_number = None
                    playback_started_at = time.perf_counter()
                    barge_in_gate.reset()
                    # The initial response clock starts after the greeting's
                    # playback task completes (client mark acknowledgement).
                    question_asked_at = None
                    no_input_reprompated = False
                    conversation_started = True

                await websocket.send_json(
                    {
                        "type": "ack",
                        "event": "start",
                    }
                )
                continue

            # ---------------------------------------------
            # Incoming audio
            # ---------------------------------------------

            if message_type == "media":
                payload = message.get(
                    "payload",
                    message.get("audio"),
                )

                if not isinstance(payload, str):
                    logger.warning(
                        "Invalid audio payload for call %s; keys=%s",
                        call_id,
                        list(message.keys()),
                    )

                    await websocket.send_json(
                        {
                            "event": "error",
                            "type": "error",
                            "message": (
                                "Media payload must be a base64 string. "
                                "Use the 'payload' or 'audio' field."
                            ),
                        }
                    )
                    continue

                try:
                    ulaw_audio, _ = decode_audio_payload(payload)

                except (
                    ValueError,
                    TypeError,
                    base64.binascii.Error,
                ):
                    await websocket.send_json(
                        {
                            "event": "error",
                            "type": "error",
                            "message": "Invalid base64 audio payload.",
                        }
                    )
                    continue

                complete_frames = frame_buffer.add_audio(ulaw_audio)
                media_payloads += 1
                media_bytes += len(ulaw_audio)
                media_frames += len(complete_frames)
                media_bad_sizes += len(ulaw_audio) % FRAME_BYTES != 0
                received_at = time.monotonic()
                if media_previous_at is not None and received_at - media_previous_at > 0.040:
                    media_gaps += 1
                media_previous_at = received_at
                if received_at - media_stats_started >= 5.0:
                    logger.debug(
                        "Media input stats: call_id=%s payloads=%d bytes=%d frames=%d "
                        "audio_seconds=%.2f non_frame_payloads=%d gaps_over_40ms=%d",
                        call_id, media_payloads, media_bytes, media_frames,
                        media_bytes / 8000, media_bad_sizes, media_gaps,
                    )
                    media_payloads = media_bytes = media_frames = media_bad_sizes = media_gaps = 0
                    media_stats_started = received_at
                logger.debug(
                    "Audio received: call_id=%s ulaw_bytes=%d complete_frames=%d",
                    call_id, len(ulaw_audio), len(complete_frames),
                )

                for frame in complete_frames:
                    # Decode ulaw frame to PCM16 for VAD (no need to
                    # re-encode to base64 – frame is already bytes).
                    from app.audio.ulaw import decode_ulaw_to_pcm16 as _dec
                    frame_pcm = _dec(frame)

                    frame_rms = vad.calculate_rms(frame_pcm)
                    speech_detected = vad.is_speech_like(frame_pcm)

                    logger.debug(
                        "Audio energy: rms=%.1f threshold=%.1f speech=%s",
                        frame_rms,
                        VAD_THRESHOLD,
                        speech_detected,
                    )

                    # Log only changes in VAD state, not every 20 ms frame.
                    if speech_detected != last_vad_speech:
                        logger.warning(
                            "VAD state changed at=%.3f call_id=%s speech=%s rms=%.1f threshold=%.1f",
                            time.time(),
                            call_id,
                            speech_detected,
                            frame_rms,
                            VAD_THRESHOLD,
                        )
                        last_vad_speech = speech_detected

                    # Barge-in: cancel playback after sustained speech.
                    if playback_task is not None and playback_task.done():
                        playback_task = None
                        playback_turn_number = None
                        playback_started_at = None
                        barge_in_gate.reset()
                        question_asked_at = arm_question_timer(
                            conversation_started, time.monotonic()
                        )
                        if question_asked_at is not None:
                            state = conversation_service.get_state(call_id)
                            logger.warning(
                                "Response timer armed at=%.3f call_id=%s turn=%s state=%s initial=%s timeout_sec=%.2f mark=%s completion_path=media",
                                time.time(), call_id, state.turn_number, state.step,
                                awaiting_first_response,
                                INITIAL_RESPONSE_TIMEOUT_SEC if awaiting_first_response else NO_INPUT_TIMEOUT_SEC,
                                getattr(websocket, "_skyfiber_last_acknowledged_mark", None),
                            )

                    barge_onset: tuple[bytes, ...] | None = None
                    if playback_audio_is_active(websocket, playback_task):
                        barge_onset = barge_in_gate.push(frame, speech_detected)

                        if barge_onset is not None:
                            interrupted_task = playback_task
                            interrupted_turn = playback_turn_number

                            interrupted_at_ms = (
                                (
                                    time.perf_counter()
                                    - playback_started_at
                                ) * 1000
                                if playback_started_at is not None
                                else None
                            )

                            playback_task = None
                            playback_turn_number = None
                            playback_started_at = None
                            # Keep the onset of a real interruption. Until
                            # the threshold is reached these frames remain a
                            # bounded candidate, which avoids transcribing
                            # ordinary playback leakage as caller speech.
                            for onset_frame in barge_onset[:-1]:
                                utterance_buffer.add_frame(onset_frame)
                            utterance_active = bool(barge_onset)
                            last_speech_time = time.monotonic()
                            no_input_reprompated = False

                            interrupted_task.cancel()
                            await asyncio.gather(
                                interrupted_task,
                                return_exceptions=True,
                            )

                            await websocket.send_json(
                                {
                                    "type": "clear",
                                    "event": "clear",
                                }
                            )

                            if (
                                interrupted_turn is not None
                                and interrupted_at_ms is not None
                            ):
                                update_turn_metric(
                                    db=db,
                                    call_id=call_id,
                                    turn_number=interrupted_turn,
                                    interrupted_at_ms=interrupted_at_ms,
                                )

                    if speech_detected:
                        # Don't buffer audio while bot is playing — echo
                        # cancellation in the browser handles most of it but
                        # adding a server-side gate prevents edge cases.
                        if not playback_audio_is_active(websocket, playback_task):
                            if not utterance_active:
                                state = conversation_service.get_state(call_id)
                                logger.warning(
                                    "Caller speech detected at=%.3f; suspending response timer call_id=%s turn=%s state=%s timer_age_sec=%.2f",
                                    time.time(),
                                    call_id,
                                    state.turn_number,
                                    state.step,
                                    max(0.0, time.monotonic() - question_asked_at)
                                    if question_asked_at is not None
                                    else -1.0,
                                )
                            utterance_active = True
                            utterance_buffer.add_frame(frame)
                            last_speech_time = time.monotonic()
                            no_input_reprompated = False

                    elif utterance_active:
                        utterance_buffer.add_frame(frame)

                    turn_complete = turn_detector.process_frame(
                        is_speech=speech_detected,
                    )

                    duration_limit_reached = (
                        utterance_active
                        and utterance_buffer.duration_seconds()
                        >= MAX_UTTERANCE_SECONDS
                    )

                    if not utterance_active or not (
                        turn_complete or duration_limit_reached
                    ):
                        continue

                    completed_utterance = utterance_buffer.get_audio()
                    utterance_duration = len(completed_utterance) / 8000
                    duration_cap_reached = duration_limit_reached
                    from app.audio.ulaw import decode_ulaw_to_pcm16
                    pcm_for_diagnostics = np.frombuffer(
                        decode_ulaw_to_pcm16(completed_utterance), dtype="<i2"
                    )
                    utterance_rms = (
                        float(np.sqrt(np.mean(pcm_for_diagnostics.astype(np.float64) ** 2)))
                        if pcm_for_diagnostics.size else 0.0
                    )
                    utterance_peak = (
                        int(np.max(np.abs(pcm_for_diagnostics.astype(np.int32))))
                        if pcm_for_diagnostics.size else 0
                    )

                    # Reset before potentially slow STT.
                    utterance_buffer.clear()
                    turn_detector.reset()
                    utterance_active = False

                    if (
                        not completed_utterance
                        or len(completed_utterance) / 8000
                        < MIN_UTTERANCE_SECONDS
                    ):
                        logger.info(
                            "Ignoring short utterance: call_id=%s duration=%.3fs",
                            call_id,
                            len(completed_utterance) / 8000,
                        )
                        continue

                    logger.info(
                        "Utterance input: call_id=%s frames=%d bytes=%d duration=%.2fs rms=%.1f peak=%d duration_cap_reached=%s",
                        call_id, len(completed_utterance) // FRAME_BYTES,
                        len(completed_utterance), utterance_duration,
                        utterance_rms, utterance_peak, duration_cap_reached,
                    )

                    response_started_at = time.perf_counter()
                    stt_started = time.perf_counter()

                    # -------------------------------------
                    # Speech-to-text
                    # -------------------------------------

                    try:
                        language = (
                            call.customer.preferred_language
                            if call.customer
                            else "en"
                        )
                        
                        logger.info(
                            "STT starting: call_id=%s bytes=%d duration=%.2fs language_config=%s model=%s device=%s compute=%s",
                            call_id, len(completed_utterance), utterance_duration,
                            language, stt_service.model_size, stt_service.device,
                            stt_service.compute_type,
                        )

                        transcription = await asyncio.wait_for(
                            asyncio.to_thread(
                                stt_service.transcribe_ulaw,
                                completed_utterance,
                                language=language,
                            ),
                            timeout=STT_TIMEOUT_SEC,
                        )

                    except Exception:
                        logger.exception(
                            "STT failed for call_id=%s",
                            call_id,
                        )
                        stt_failure_count += 1
                        if stt_failure_count < 2:
                            retry_text = (
                                "I had trouble hearing that. "
                                "Could you say your answer once more?"
                            )
                            playback_task = await save_and_send_bot_reply(
                                websocket=websocket,
                                db=db,
                                call_id=call_id,
                                text=retry_text,
                            )
                            playback_turn_number = None
                            playback_started_at = time.perf_counter()
                            question_asked_at = None
                            continue

                        partial = get_partial_result(call_id, outcome="error")
                        finish_call(
                            db=db,
                            call=call,
                            status="failed",
                            reason="stt_error",
                            result=partial,
                        )
                        playback_task = await save_and_send_bot_reply(
                            websocket=websocket,
                            db=db,
                            call_id=call_id,
                            text="I'm sorry, I can't hear you clearly. Goodbye.",
                        )
                        if not await finish_playback_while_receiving(
                            websocket, playback_task, call_id
                        ):
                            return
                        await send_terminal_event(websocket, call_id, "stt_error")
                        return

                    stt_ms = (
                        time.perf_counter() - stt_started
                    ) * 1000

                    raw_caller_text = (
                        transcription.get("text") or ""
                    ).strip()
                    caller_text = " ".join(raw_caller_text.replace("’", "'").split())
                    stt_failure_count = 0
                    
                    logger.info(
                        "STT result: call_id=%s chars=%d normalized=%s fingerprint=%s language=%s probability=%.3f duration_ms=%.1f",
                        call_id,
                        len(caller_text),
                        caller_text != raw_caller_text,
                        hashlib.sha256(caller_text.encode("utf-8")).hexdigest()[:12],
                        transcription.get("language"),
                        float(transcription.get("language_probability") or 0.0),
                        stt_ms,
                    )

                    if not caller_text:
                        logger.info(
                            "STT returned empty transcript: call_id=%s language=%s duration=%.2fs",
                            call_id,
                            language,
                            len(completed_utterance) / 8000,
                        )
                        await websocket.send_json({
                            "type": "ack",
                            "event": "empty_transcription",
                        })
                        playback_task = await save_and_send_bot_reply(
                            websocket=websocket,
                            db=db,
                            call_id=call_id,
                            text="Sorry, I couldn't make that out. Please say your answer again.",
                        )
                        playback_turn_number = None
                        playback_started_at = time.perf_counter()
                        question_asked_at = None
                        no_input_reprompated = False
                        continue

                    awaiting_first_response = False

                    # Save caller transcript.
                    turn_number = await save_and_send_transcript(
                        websocket=websocket,
                        db=db,
                        call_id=call_id,
                        role="caller",
                        text=caller_text,
                        stt_ms=stt_ms,
                    )

                    # -------------------------------------
                    # Initialize conversation if needed
                    # -------------------------------------

                    if not conversation_started:
                        greeting = conversation_service.start(
                            call_id=call_id,
                            customer_id=call.customer_id,
                            language=call.customer.preferred_language if call.customer else "en",
                        )

                        playback_task = await save_and_send_bot_reply(
                            websocket=websocket,
                            db=db,
                            call_id=call_id,
                            text=greeting.text,
                        )

                        playback_turn_number = None
                        playback_started_at = time.perf_counter()
                        barge_in_gate.reset()

                        # If the caller spoke before the start event,
                        # finish the greeting before processing their reply.
                        if not await finish_playback_while_receiving(
                            websocket, playback_task, call_id
                        ):
                            return

                        playback_task = None
                        playback_turn_number = None
                        playback_started_at = None
                        barge_in_gate.reset()
                        conversation_started = True

                    # -------------------------------------
                    # LLM extraction and fallback
                    # -------------------------------------

                    state = conversation_service.get_state(call_id)

                    if state is None:
                        logger.error(
                            "Conversation state missing for call_id=%s",
                            call_id,
                        )
                        continue

                    expected_field = get_expected_field(state)
                    (
                        extracted_candidates,
                        llm_extraction_attempted,
                        llm_ms,
                        parser_selected,
                        fallback_reason,
                    ) = await extract_turn_candidates(
                        user_text=caller_text,
                        state=state,
                        expected_field=expected_field,
                        extractor=llm_service,
                    )

                    if turn_number is not None:
                        update_turn_metric(
                            db=db,
                            call_id=call_id,
                            turn_number=turn_number,
                            llm_ms=llm_ms,
                        )

                    # -------------------------------------
                    # Generate conversational response
                    # -------------------------------------

                    reply = conversation_service.handle_turn(
                        call_id=call_id,
                        user_text=caller_text,
                        extracted=extracted_candidates,
                    )

                    logger.info(
                        "Answer extraction: call_id=%s turn=%s expected=%s method=%s parser=%s fallback_reason=%s elapsed_ms=%.1f result=%s accepted_fields=%s reply_action=%s retry_count=%s",
                        call_id, turn_number, expected_field,
                        "llm" if llm_extraction_attempted else "deterministic",
                        parser_selected, fallback_reason, llm_ms,
                        "accepted" if extracted_candidates and expected_field in extracted_candidates else "clarify",
                        sorted(extracted_candidates.keys()) if extracted_candidates else [],
                        reply.action,
                        state.retries.get(expected_field, 0) if expected_field else 0,
                    )

                    # ----------------------------------------------------------
                    # LLM generates the wording; code already decided the action.
                    # Only override text for "continue" turns (non-terminal).
                    # Terminal closings (hangup/callback/opt_out) still use the
                    # code-controlled text to guarantee required info is included.
                    # ----------------------------------------------------------
                    if ENABLE_LLM_REPLY_GENERATION and reply.action == "continue" and state.language == "en" and llm_extraction_attempted and not (
                        expected_field in {"technician_rating", "nps"}
                        and len(caller_text.split()) <= 2
                    ):
                        post_state = conversation_service.get_state(call_id)
                        open_goals = [
                            f
                            for f in (
                                "issue_resolved", "still_facing",
                                "technician_on_time", "technician_rating",
                                "nps", "other_feedback",
                            )
                            if post_state.feedback.get(f) is None
                            and f not in post_state.unclear_fields
                        ]
                        cust = call.customer
                        reply_generation_started = time.perf_counter()
                        try:
                            llm_spoken = await llm_reply_service.generate_reply(
                                customer_name=cust.name if cust else "there",
                                issue_type=cust.issue_type if cust else "",
                                ticket_id=cust.ticket_id if cust else "",
                                visit_date=cust.visit_date if cust else "",
                                promised_slot=cust.promised_slot if cust else "",
                                technician_name=cust.technician_name if cust else "",
                                feedback_so_far=post_state.feedback,
                                open_goals=open_goals,
                                caller_last_said=caller_text,
                                conversation_step=post_state.step,
                            )
                            # Code overrides: never accept an empty string.
                            if llm_spoken and llm_spoken.strip():
                                reply = type(reply)(
                                    text=llm_spoken,
                                    action=reply.action,
                                    state=reply.state,
                                    result=reply.result,
                                )
                        except Exception:
                            logger.warning(
                                "LLM reply generation failed; using fallback text",
                                exc_info=True,
                            )
                        reply_generation_ms = (
                            time.perf_counter() - reply_generation_started
                        ) * 1000
                        if turn_number is not None:
                            update_turn_metric(
                                db=db,
                                call_id=call_id,
                                turn_number=turn_number,
                                llm_ms=llm_ms + reply_generation_ms,
                            )

                    playback_task = await save_and_send_bot_reply(
                        websocket=websocket,
                        db=db,
                        call_id=call_id,
                        text=reply.text,
                        turn_number=turn_number,
                        response_started_at=response_started_at,
                    )

                    playback_turn_number = turn_number
                    playback_started_at = time.perf_counter()
                    barge_in_gate.reset()
                    # Arm only after TTS has finished and its playback mark is
                    # acknowledged. Provider generation time is not caller silence.
                    question_asked_at = None
                    no_input_reprompated = False
                                        # -------------------------------------
                    # Persist any result
                    # -------------------------------------

                    if reply.result is not None:
                        call.result = reply.result
                        call.outcome = reply.result.get("outcome")

                        call.needs_followup = reply.result.get(
                            "needs_followup",
                            reply.action == "callback",
                        )

                        db.commit()

                    # -------------------------------------
                    # Handle terminal outcomes
                    # -------------------------------------

                    if reply.action == "opt_out":
                        finish_call(
                            db=db,
                            call=call,
                            status="opted_out",
                            reason="opt_out",
                            result=reply.result,
                        )

                    elif reply.action == "callback":
                        finish_call(
                            db=db,
                            call=call,
                            status="completed",
                            reason="callback_requested",
                            result=reply.result,
                        )

                    elif reply.action == "hangup":
                        outcome = (
                            reply.result.get("outcome")
                            if reply.result
                            else "conversation_ended"
                        )

                        finish_call(
                            db=db,
                            call=call,
                            status="completed",
                            reason=outcome,
                            result=reply.result,
                        )

                    if reply.action in {
                        "hangup",
                        "callback",
                        "opt_out",
                    }:
                        # Let the final spoken message finish before closing.
                        if not await finish_playback_while_receiving(
                            websocket, playback_task, call_id
                        ):
                            return
                        playback_task = None
                        playback_turn_number = None
                        playback_started_at = None
                        await send_terminal_event(
                            websocket, call_id, call.end_reason or "completed"
                        )
                        return

                # Acknowledge the received media message.
                await websocket.send_json(
                    {
                        "type": "ack",
                        "event": "media",
                    }
                )
                continue

            # ---------------------------------------------
            # Playback mark
            # ---------------------------------------------

            if message_type == "mark":
                acknowledge_playback_mark(websocket, message)
                continue

            # ---------------------------------------------
            # Caller hangs up
            # ---------------------------------------------

            if message_type == "hangup":
                if (
                    playback_task is not None
                    and not playback_task.done()
                ):
                    playback_task.cancel()
                    await asyncio.gather(
                        playback_task,
                        return_exceptions=True,
                    )
                    playback_task = None

                partial_result = get_partial_result(
                    call_id,
                    outcome="caller_hangup",
                )

                finish_call(
                    db=db,
                    call=call,
                    status="completed",
                    reason="caller_hangup",
                    result=partial_result,
                )

                await send_terminal_event(websocket, call_id, "caller_hangup")
                return

            # ---------------------------------------------
            # Unsupported WebSocket event
            # ---------------------------------------------

            await websocket.send_json(
                {
                    "event": "error",
                    "type": "error",
                    "message": (
                        f"Unsupported message type: {message_type}"
                    ),
                }
            )

    except WebSocketDisconnect:
        # Preserve partial feedback if the browser disconnects unexpectedly.
        try:
            call = db.get(Call, call_id)

            if call is not None and call.status == "in_progress":
                partial_result = get_partial_result(
                    call_id,
                    outcome="disconnected",
                )

                finish_call(
                    db=db,
                    call=call,
                    status="completed",
                    reason="websocket_disconnected",
                    result=partial_result,
                )

        except Exception:
            db.rollback()
            logger.exception(
                "Failed to save disconnected call_id=%s",
                call_id,
            )

    except Exception:
        db.rollback()

        logger.exception(
            "Unexpected WebSocket error for call_id=%s",
            call_id,
        )

        try:
            call = db.get(Call, call_id)
            if call is not None and call.status == "in_progress":
                finish_call(
                    db=db,
                    call=call,
                    status="failed",
                    reason="server_error",
                    result=get_partial_result(call_id, outcome="error"),
                )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to save server-error call_id=%s",
                call_id,
            )

        try:
            await websocket.send_json(
                {
                    "event": "error",
                    "type": "error",
                    "message": (
                        "An unexpected server error occurred. "
                        "Check the API terminal for the traceback."
                    ),
                }
            )

            await websocket.close(code=1011)

        except Exception:
            logger.debug("WebSocket closed before error notification: %s", call_id)

    finally:
        if playback_task is not None and not playback_task.done():
            playback_task.cancel()

            try:
                await asyncio.gather(
                    playback_task,
                    return_exceptions=True,
                )
            except Exception:
                logger.debug(
                    "Playback cleanup failed for %s",
                    call_id,
                )

        db.close()
        conversation_service.forget(call_id)
