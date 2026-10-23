"""
LLM-driven conversational reply generation for the SkyFiber voice agent.

The LLM generates natural spoken replies. ConversationService remains
responsible for conversation flow, validation, and deciding what happens next.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)
REPLY_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "feedback_reply.md"
)

OLLAMA_BASE_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://127.0.0.1:11434",
)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Allow sufficient time for a local model to generate a reply.
LLM_TIMEOUT = float(os.getenv("LLM_REPLY_TIMEOUT", "6.0"))
LLM_CONNECT_TIMEOUT = float(os.getenv("LLM_CONNECT_TIMEOUT", "3.0"))

_FALLBACK_QUESTIONS: dict[str, str] = {
    "identity_confirmed": (
        "Could you please confirm you're the account holder?"
    ),
    "issue_resolved": (
        "Has your internet issue been fully resolved?"
    ),
    "still_facing": (
        "Are you still experiencing any internet problems?"
    ),
    "technician_on_time": (
        "Did the technician arrive in the promised time slot?"
    ),
    "technician_rating": (
        "On a scale of one to five, how would you rate the technician?"
    ),
    "nps": (
        "On a scale of zero to ten, how likely are you to recommend SkyFiber?"
    ),
    "other_feedback": (
        "Is there anything else you'd like to share with us?"
    ),
}

class LLMReplyService:
    """Generate natural bot replies using a local Ollama model."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: float = LLM_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def generate_reply(
        self,
        *,
        customer_name: str,
        issue_type: str,
        ticket_id: str,
        visit_date: str,
        promised_slot: str,
        technician_name: str,
        feedback_so_far: dict[str, Any],
        open_goals: list[str],
        caller_last_said: str,
        conversation_step: str,
        needs_followup: bool = False,
        callback_requested: bool = False,
        wrong_person: bool = False,
        opted_out: bool = False,
    ) -> str:
        """Generate a spoken reply, falling back safely if Ollama fails."""

        context = {
            "customer_name": customer_name,
            "issue_type": issue_type,
            "ticket_id": ticket_id,
            "visit_date": visit_date,
            "promised_slot": promised_slot,
            "technician_name": technician_name,
            "feedback_collected": feedback_so_far,
            "open_goals": open_goals,
            "caller_last_said": caller_last_said,
            "current_step": conversation_step,
            "flags": {
                "needs_followup": needs_followup,
                "callback_requested": callback_requested,
                "wrong_person": wrong_person,
                "opted_out": opted_out,
            },
        }

        prompt = (
            "Customer and conversation context:\n"
            f"{json.dumps(context, ensure_ascii=False)}\n\n"
            "Generate the agent's next reply based on the current step. "
            "Return only the spoken text."
        )

        try:
            system_prompt = REPLY_PROMPT_PATH.read_text(encoding="utf-8")
            timeout = httpx.Timeout(
                timeout=self.timeout,
                connect=LLM_CONNECT_TIMEOUT,
                read=self.timeout,
                write=5.0,
                pool=3.0,
            )

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "stream": False,
                        "options": {
                            "temperature": 0.5,
                            "num_predict": 40,
                        },
                        "messages": [
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {
                                "role": "user",
                                "content": prompt,
                            },
                        ],
                    },
                )
                response.raise_for_status()
                payload = response.json()

            message = payload.get("message", {})
            raw_text = message.get("content", "")

            if not isinstance(raw_text, str) or not raw_text.strip():
                logger.warning("Ollama returned an empty reply.")
                return _fallback_for_goals(
                    open_goals,
                    conversation_step,
                )

            cleaned_text = _clean_spoken_text(raw_text)

            if cleaned_text:
                return cleaned_text

            logger.warning("Ollama reply was empty after cleaning.")

        except httpx.TimeoutException:
            logger.warning(
                "Ollama timed out after %.1f seconds; using fallback.",
                self.timeout,
            )
        except httpx.ConnectError:
            logger.exception(
                "Cannot connect to Ollama at %s; using fallback.",
                self.base_url,
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Ollama returned HTTP %s; using fallback.",
                exc.response.status_code,
            )
        except (httpx.RequestError, ValueError, KeyError, TypeError):
            logger.exception(
                "Ollama response failed; using fallback."
            )
        except Exception:
            logger.exception(
                "Unexpected LLM reply error; using fallback."
            )

        return _fallback_for_goals(open_goals, conversation_step)


def _clean_spoken_text(text: str) -> str:
    """Remove common formatting artifacts and limit replies to two sentences."""

    text = text.strip()

    # Remove common model prefixes and surrounding markdown.
    text = re.sub(
        r"^(?:assistant|agent|reply)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.lstrip("#*-`> \n\t")
    text = text.replace("**", "").replace("__", "")
    text = text.replace("`", "")
    text = re.sub(r"\s+", " ", text).strip()

    sentences = _split_sentences(text)
    return " ".join(sentences[:2]).strip()


def _split_sentences(text: str) -> list[str]:
    """Split short spoken replies into sentences."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _fallback_for_goals(
    open_goals: list[str],
    step: str,
) -> str:
    """Provide a deterministic question if LLM generation fails."""

    if step == "identity":
        return _FALLBACK_QUESTIONS["identity_confirmed"]

    for goal in open_goals:
        if goal in _FALLBACK_QUESTIONS:
            return _FALLBACK_QUESTIONS[goal]

    return (
        "Thank you for your feedback. "
        "Is there anything else you'd like to share?"
    )
