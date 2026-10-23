
"""Collect audio frames belonging to one caller utterance."""

class UtteranceBuffer:
    def __init__(self, max_duration_seconds: float = 30.0, sample_rate: int = 8000):
        if max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")

        self.max_bytes = int(max_duration_seconds * sample_rate)
        self._audio = bytearray()

    def add_frame(self, frame: bytes) -> None:
        """Append one μ-law audio frame, enforcing the size limit."""
        if not isinstance(frame, bytes):
            raise TypeError("frame must be bytes")

        if len(self._audio) + len(frame) > self.max_bytes:
            raise ValueError("Utterance exceeds maximum duration")

        self._audio.extend(frame)

    def get_audio(self) -> bytes:
        """Return a snapshot of the buffered audio."""
        return bytes(self._audio)

    def duration_seconds(self) -> float:
        """Estimate duration for 8 kHz, mono, 8-bit μ-law audio."""
        return len(self._audio) / 8000

    def clear(self) -> None:
        """Clear audio after the utterance has been processed."""
        self._audio.clear()

    def __len__(self) -> int:
        return len(self._audio)
