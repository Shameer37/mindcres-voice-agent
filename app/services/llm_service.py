
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "feedback_extraction.txt"
)


class LLMExtractionService:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        self.base_url = (base_url or os.getenv(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        )).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
        self.timeout = timeout or float(os.getenv("LLM_TIMEOUT", "5"))

    async def extract(
        self,
        user_text: str,
        current_feedback: dict[str, Any] | None = None,
        expected_field: str | None = None,
        language: str = "en",
    ) -> dict[str, Any] | None:
        """
        Extract feedback fields from the latest caller utterance.

        Returns None when Ollama is unavailable or its response
        cannot be parsed, allowing deterministic fallback logic.
        """
        try:
            started = time.perf_counter()
            system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

            context = {
                "current_feedback": current_feedback or {},
                "expected_field": expected_field,
                "customer_language": language,
                "latest_caller_utterance": user_text,
            }

            request_started = time.perf_counter()
            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": 0},
                        "messages": [
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {
                                "role": "user",
                                "content": json.dumps(context),
                            },
                        ],
                    },
                )

                response.raise_for_status()
                payload = response.json()
            request_ms = (time.perf_counter() - request_started) * 1000

            parse_started = time.perf_counter()
            content = payload["message"]["content"]
            result = json.loads(content)
            parse_ms = (time.perf_counter() - parse_started) * 1000
            timings = payload.get("total_duration"), payload.get("load_duration"), payload.get("eval_duration")
            to_ms = lambda value: (value / 1_000_000) if isinstance(value, (int, float)) else None
            logger.info(
                "LLM extraction timing model=%s request_ms=%.1f parse_ms=%.1f client_total_ms=%.1f "
                "server_total_ms=%s model_load_ms=%s model_eval_ms=%s",
                self.model, request_ms, parse_ms, (time.perf_counter() - started) * 1000,
                to_ms(timings[0]), to_ms(timings[1]), to_ms(timings[2]),
            )

            if not isinstance(result, dict):
                return None

            allowed_fields = {
                "identity_confirmed",
                "issue_resolved",
                "still_facing",
                "technician_on_time",
                "technician_rating",
                "nps",
                "other_feedback",
            }

            return {
                key: result[key]
                for key in allowed_fields
                if key in result
            }

        except httpx.TimeoutException:
            logger.warning("LLM extraction failed category=timeout stage=http_request")
            return None
        except (
            httpx.HTTPError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ):
            logger.warning("LLM extraction failed category=invalid_response_or_transport")
            return None
