
import asyncio
import hashlib
import logging
import os
import subprocess
import time
from collections import OrderedDict
from threading import Lock

import edge_tts
import imageio_ffmpeg

from app.audio.ulaw import (
    encode_pcm16_to_ulaw,
    split_ulaw_frames,
)

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"
LANGUAGE_VOICES = {
    "en": DEFAULT_VOICE,
    "hi": "hi-IN-SwaraNeural",
    "te": "te-IN-ShrutiNeural",
}
SAMPLE_RATE = 8000
FRAME_BYTES = 160
PROVIDER_TIMEOUT_SEC = float(os.getenv("TTS_PROVIDER_TIMEOUT_SEC", "12"))


class TTSService:
    """Generate speech and convert it to 8 kHz G.711 μ-law audio."""

    def __init__(self, voice: str = DEFAULT_VOICE) -> None:
        self.voice = voice
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self.timeout_seconds = PROVIDER_TIMEOUT_SEC
        self._frame_cache: OrderedDict[tuple[str, bytes], tuple[bytes, ...]] = OrderedDict()
        self._frame_cache_bytes = 0
        self._frame_cache_limit_bytes = int(os.getenv("TTS_FRAME_CACHE_BYTES", str(4 * 1024 * 1024)))
        self._frame_cache_lock = Lock()

    async def synthesize_pcm16(self, text: str, language: str = "en") -> bytes:
        """Return mono PCM16 audio at 8 kHz."""

        text = text.strip()
        if not text:
            return b""

        # Generate speech as MP3 bytes using Edge TTS.
        communicate = edge_tts.Communicate(
            text=text,
            voice=LANGUAGE_VOICES.get(language, self.voice),
        )

        provider_started = time.perf_counter()

        async def collect_audio() -> list[bytes]:
            chunks = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    chunks.append(chunk["data"])
            return chunks

        try:
            mp3_chunks = await asyncio.wait_for(
                collect_audio(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "Edge TTS timed out after %.1f seconds (voice=%s)",
                self.timeout_seconds, LANGUAGE_VOICES.get(language, self.voice),
            )
            raise RuntimeError(
                f"Edge TTS provider timed out after {self.timeout_seconds:.1f}s; "
                "check network/DNS access and retry."
            ) from exc
        except Exception as exc:
            logger.warning("TTS failed stage=provider error_type=%s", type(exc).__name__)
            raise

        mp3_audio = b"".join(mp3_chunks)
        provider_ms = (time.perf_counter() - provider_started) * 1000

        if not mp3_audio:
            raise RuntimeError("TTS returned no audio.")

        # Convert MP3 to mono, 8 kHz, signed PCM16.
        # Running FFmpeg in a worker thread avoids Windows asyncio
        # subprocess compatibility issues.
        command = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "pipe:1",
        ]

        conversion_started = time.perf_counter()
        try:
            process = await asyncio.to_thread(
                subprocess.run,
                command,
                input=mp3_audio,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("FFmpeg TTS conversion timed out after %.1f seconds", self.timeout_seconds)
            raise RuntimeError(
                f"FFmpeg TTS conversion timed out after {self.timeout_seconds:.1f}s."
            ) from exc
        except Exception as exc:
            logger.warning("TTS failed stage=conversion error_type=%s", type(exc).__name__)
            raise

        if process.returncode != 0:
            logger.error(
                "TTS failed stage=conversion error_type=FFmpegNonZeroExit returncode=%d",
                process.returncode,
            )
            raise RuntimeError(
                "Could not convert generated speech."
            )

        pcm_audio = process.stdout

        # PCM16 requires two bytes per sample.
        if len(pcm_audio) % 2:
            pcm_audio = pcm_audio[:-1]

        conversion_ms = (time.perf_counter() - conversion_started) * 1000
        logger.info(
            "TTS synthesis complete voice=%s chars=%d provider_ms=%.1f conversion_ms=%.1f mp3_bytes=%d pcm_bytes=%d",
            LANGUAGE_VOICES.get(language, self.voice), len(text), provider_ms,
            conversion_ms, len(mp3_audio), len(pcm_audio),
        )
        return pcm_audio

    async def synthesize_ulaw(self, text: str, language: str = "en") -> bytes:
        """Return 8 kHz G.711 μ-law audio."""

        pcm_audio = await self.synthesize_pcm16(text, language=language)

        if not pcm_audio:
            return b""

        return encode_pcm16_to_ulaw(pcm_audio)

    async def synthesize_frames(self, text: str, language: str = "en") -> list[bytes]:
        """Return 20 ms μ-law frames, each exactly 160 bytes."""

        text = text.strip()
        if not text:
            return []
        voice = LANGUAGE_VOICES.get(language, self.voice)
        key = (voice, hashlib.sha256(text.encode("utf-8")).digest())
        with self._frame_cache_lock:
            cached = self._frame_cache.get(key)
            if cached is not None:
                self._frame_cache.move_to_end(key)
                logger.info("TTS frame cache hit voice=%s chars=%d frames=%d", voice, len(text), len(cached))
                return list(cached)

        ulaw_audio = await self.synthesize_ulaw(text, language=language)

        if not ulaw_audio:
            return []

        frame_started = time.perf_counter()
        frames = list(
            split_ulaw_frames(
                ulaw_audio,
                frame_size=FRAME_BYTES,
                pad_final=True,
            )
        )
        frame_tuple = tuple(frames)
        frame_ms = (time.perf_counter() - frame_started) * 1000
        logger.info("TTS frame preparation complete frames=%d frame_ms=%.1f", len(frames), frame_ms)
        size_bytes = sum(map(len, frame_tuple))
        if size_bytes <= self._frame_cache_limit_bytes:
            with self._frame_cache_lock:
                previous = self._frame_cache.pop(key, None)
                if previous is not None:
                    self._frame_cache_bytes -= sum(map(len, previous))
                self._frame_cache[key] = frame_tuple
                self._frame_cache_bytes += size_bytes
                while self._frame_cache_bytes > self._frame_cache_limit_bytes:
                    _, evicted = self._frame_cache.popitem(last=False)
                    self._frame_cache_bytes -= sum(map(len, evicted))
        return frames
