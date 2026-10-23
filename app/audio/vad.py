
"""Simple energy-based voice activity detection for PCM16 audio."""

import struct

import numpy as np


class SustainedSpeechGate:
    """Collect sustained voiced frames across bounded word-boundary gaps."""

    def __init__(
        self,
        min_speech_ms: int = 280,
        max_gap_ms: int = 160,
        frame_ms: int = 20,
    ) -> None:
        if min_speech_ms <= 0 or max_gap_ms < 0 or frame_ms <= 0:
            raise ValueError("speech and frame durations must be positive")
        self.min_speech_ms = min_speech_ms
        self.max_gap_ms = max_gap_ms
        self.frame_ms = frame_ms
        self.reset()

    def reset(self) -> None:
        self.speech_ms = 0
        self.gap_ms = 0
        self.frames: list[bytes] = []

    def push(self, frame: bytes, speech_like: bool) -> tuple[bytes, ...] | None:
        """Return the retained onset frames once enough speech is observed."""
        if speech_like:
            self.speech_ms += self.frame_ms
            self.gap_ms = 0
            self.frames.append(frame)
        elif self.frames:
            self.gap_ms += self.frame_ms
            self.frames.append(frame)
            if self.gap_ms > self.max_gap_ms:
                self.reset()
                return None
        else:
            return None

        if self.speech_ms >= self.min_speech_ms:
            onset = tuple(self.frames)
            self.reset()
            return onset
        return None


class EnergyVAD:
    """Classify PCM16 frames using RMS energy."""

    def __init__(self, threshold: float = 500.0) -> None:
        if threshold < 0:
            raise ValueError("threshold must be non-negative")

        self.threshold = threshold

    def calculate_rms(self, pcm16: bytes) -> float:
        """Calculate RMS energy for signed 16-bit PCM audio."""
        if len(pcm16) % 2 != 0:
            raise ValueError(
                "PCM16 audio must contain complete 2-byte samples"
            )

        sample_count = len(pcm16) // 2
        if sample_count == 0:
            return 0.0

        samples = struct.unpack(f"<{sample_count}h", pcm16)
        mean_square = sum(s * s for s in samples) / sample_count
        return mean_square ** 0.5

    def is_speech(self, pcm16: bytes) -> bool:
        """Classify a frame using the configured RMS threshold."""
        return self.calculate_rms(pcm16) >= self.threshold

    def is_speech_like(self, pcm16: bytes) -> bool:
        """Require energy plus voiced periodicity for interruption/timer evidence.

        This conservative 8 kHz proxy rejects isolated impulses and broadband
        stationary noise while retaining voiced portions of caller speech.
        It is not a replacement for a trained VAD in noisy acoustic settings.
        """
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
        if samples.size < 80 or self.calculate_rms(pcm16) < self.threshold:
            return False
        samples -= samples.mean()
        energy = float(np.dot(samples, samples))
        if energy <= 0:
            return False
        # Human voiced pitch at telephone rates is typically 50–200 Hz,
        # corresponding to lags of 40–160 samples at 8 kHz.
        periodicity = max(
            float(np.dot(samples[:-lag], samples[lag:])) / energy
            for lag in range(40, min(161, samples.size - 1))
        )
        return periodicity >= 0.25
