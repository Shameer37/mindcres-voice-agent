
"""Local speech-to-text service using faster-whisper."""

import logging
import os
from time import perf_counter
from threading import Lock

import numpy as np
from faster_whisper import WhisperModel

from app.audio.resample import resample_pcm16
from app.audio.ulaw import decode_ulaw_to_pcm16

logger = logging.getLogger(__name__)

# One-factor-at-a-time variants. The baseline values below match the original
# production call exactly; selecting a variant is explicitly opt-in.
EXPERIMENTS = {
    "baseline": {},
    "beam3": {"beam_size": 3},
    "auto_language": {"auto_language": True},
    "condition_context": {"condition_on_previous_text": True},
    "vad_filter": {"vad_filter": True},
    "base_model": {"model_size": "base"},
}


class STTService:
    """Transcribe G.711 μ-law telephony audio using a local Whisper model."""

    def __init__(
        self,
        model_size: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        local_files_only: bool = True,
        experiment: str | None = None,
    ) -> None:
        # Defer model loading until the first actual transcription. The
        # default refuses network downloads; setup is an explicit command.
        self.experiment = experiment or os.getenv("STT_EXPERIMENT", "baseline")
        if self.experiment not in EXPERIMENTS:
            raise ValueError(f"Unknown STT_EXPERIMENT: {self.experiment}")
        overrides = EXPERIMENTS[self.experiment]
        self.model_size = overrides.get("model_size", model_size)
        self.device = device
        self.compute_type = compute_type
        self.local_files_only = local_files_only
        self.model = None
        self._lock = Lock()
        logger.info(
            "STT configured experiment=%s model=%s device=%s compute_type=%s",
            self.experiment, self.model_size, self.device, self.compute_type,
        )

    def _get_model(self):
        if self.model is None:
            self.model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=self.local_files_only,
            )
        return self.model

    def transcribe_ulaw(
        self,
        ulaw_audio: bytes,
        language: str | None = None,
    ) -> dict:
        """Transcribe an utterance supplied as 8 kHz μ-law bytes."""
        if not ulaw_audio:
            return {
                "text": "",
                "language": language,
                "language_probability": 0.0,
            }

        preprocess_started = perf_counter()
        # μ-law 8 kHz -> PCM16 8 kHz.
        pcm16_8k = decode_ulaw_to_pcm16(ulaw_audio)

        # PCM16 8 kHz -> PCM16 16 kHz.
        pcm16_16k = resample_pcm16(
            pcm16_8k,
            original_rate=8000,
            target_rate=16000,
        )

        # Convert little-endian PCM16 bytes to float32 samples in [-1, 1].
        audio = (
            np.frombuffer(pcm16_16k, dtype="<i2").astype(np.float32)
            / 32768.0
        )
        preprocess_ms = (perf_counter() - preprocess_started) * 1000

        # Whisper supports language codes such as en, hi, and te.
        started = perf_counter()
        experiment = getattr(self, "experiment", "baseline")
        variant = EXPERIMENTS[experiment]
        model_size = getattr(self, "model_size", "tiny")
        options = {
            "language": None if variant.get("auto_language") else language,
            "beam_size": variant.get("beam_size", 1),
            "vad_filter": variant.get("vad_filter", False),
            "vad_parameters": {
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 150,
            },
            "condition_on_previous_text": variant.get(
                "condition_on_previous_text", False
            ),
            "no_speech_threshold": 0.6,
            "log_prob_threshold": -1.0,
            "compression_ratio_threshold": 2.4,
        }
        lock_started = perf_counter()
        with self._lock:
            lock_wait_ms = (perf_counter() - lock_started) * 1000
            init_ms = 0.0
            init_started = perf_counter()
            was_loaded = self.model is not None
            model = self._get_model()
            if not was_loaded:
                init_ms = (perf_counter() - init_started) * 1000
            inference_started = perf_counter()
            segments, info = model.transcribe(audio, **options)

            # Segments are generated lazily, so consume them before releasing
            # the lock.
            segment_rows = []
            for segment in segments:
                segment_text = segment.text.strip()
                if segment_text:
                    segment_rows.append({
                        "text": segment_text,
                        "start": getattr(segment, "start", None),
                        "end": getattr(segment, "end", None),
                        "avg_logprob": getattr(segment, "avg_logprob", None),
                        "no_speech_prob": getattr(segment, "no_speech_prob", None),
                    })
            text = " ".join(row["text"] for row in segment_rows).strip()
            inference_ms = (perf_counter() - inference_started) * 1000

        duration_ms = (perf_counter() - started) * 1000
        logger.debug(
            "STT transcription experiment=%s model=%s input_rate_hz=8000 input_format=mulaw8k "
            "model_rate_hz=16000 model_format=float32 audio_duration_ms=%.1f "
            "preprocess_ms=%.1f lock_wait_ms=%.1f model_init_ms=%.1f inference_ms=%.1f "
            "stt_ms=%.1f "
            "config=%s transcript=%r segments=%s",
            experiment, model_size, len(ulaw_audio) / 8, preprocess_ms,
            lock_wait_ms, init_ms, inference_ms, duration_ms,
            options, text, segment_rows,
        )

        return {
            "text": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "experiment": experiment,
            "model_size": model_size,
            "stt_ms": duration_ms,
            "preprocess_ms": preprocess_ms,
            "lock_wait_ms": lock_wait_ms,
            "model_init_ms": init_ms,
            "inference_ms": inference_ms,
            "segments": segment_rows,
        }
