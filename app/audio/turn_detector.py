
"""Detect the end of a caller's speech using consecutive silent frames."""

import math


class TurnDetector:
    """Mark a caller turn complete after speech followed by enough silence."""

    def __init__(
        self,
        silence_duration_ms: int = 600,
        frame_duration_ms: int = 20,
    ) -> None:
        if silence_duration_ms <= 0:
            raise ValueError("silence_duration_ms must be positive")

        if frame_duration_ms <= 0:
            raise ValueError("frame_duration_ms must be positive")

        self.frame_duration_ms = frame_duration_ms
        self.required_silent_frames = math.ceil(
            silence_duration_ms / frame_duration_ms
        )
        self.reset()

    def process_frame(self, is_speech: bool) -> bool:
        """
        Process one audio frame.

        Returns True once speech has been followed by enough silence.
        """
        if is_speech:
            self.speech_started = True
            self.silent_frames = 0
            return False

        if not self.speech_started:
            return False

        self.silent_frames += 1

        if self.silent_frames >= self.required_silent_frames:
            self.reset()
            return True

        return False

    def reset(self) -> None:
        """Reset the detector for a new caller turn."""
        self.speech_started = False
        self.silent_frames = 0
