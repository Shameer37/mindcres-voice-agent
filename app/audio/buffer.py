
"""Buffer μ-law audio and extract complete fixed-size frames."""

from collections.abc import Iterable

from app.audio.ulaw import ULAW_FRAME_BYTES


class AudioFrameBuffer:
    """Accumulate audio bytes and emit complete frames."""

    def __init__(self, frame_size: int = ULAW_FRAME_BYTES) -> None:
        if frame_size <= 0:
            raise ValueError("frame_size must be positive")

        self.frame_size = frame_size
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        """Return the number of bytes waiting for a complete frame."""
        return len(self._buffer)

    def add_audio(self, audio: bytes) -> list[bytes]:
        """
        Append audio and return all complete frames.

        Any incomplete frame remains buffered for the next audio chunk.
        """
        if not isinstance(audio, bytes):
            raise TypeError("audio must be bytes")

        self._buffer.extend(audio)
        frames: list[bytes] = []

        while len(self._buffer) >= self.frame_size:
            frames.append(bytes(self._buffer[:self.frame_size]))
            del self._buffer[:self.frame_size]

        return frames

    def reset(self) -> None:
        """Discard buffered bytes, for example when a caller turn ends."""
        self._buffer.clear()
