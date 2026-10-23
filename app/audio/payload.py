
"""Decode Base64-encoded G.711 μ-law audio payloads."""

import base64
import binascii

from app.audio.ulaw import decode_ulaw_to_pcm16


class AudioPayloadError(ValueError):
    """Raised when an incoming audio payload is invalid."""


def decode_audio_payload(payload: str) -> tuple[bytes, bytes]:
    """
    Decode a Base64 μ-law payload.

    Returns:
        A tuple of (raw_ulaw_audio, pcm16_audio).

    PCM16 output is signed 16-bit little-endian audio.
    This function does not resample the audio.
    """
    if not isinstance(payload, str):
        raise AudioPayloadError("Audio payload must be a Base64 string")

    if not payload:
        raise AudioPayloadError("Audio payload cannot be empty")

    try:
        ulaw_audio = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AudioPayloadError("Audio payload is not valid Base64") from exc

    if not ulaw_audio:
        raise AudioPayloadError("Decoded audio payload cannot be empty")

    pcm16_audio = decode_ulaw_to_pcm16(ulaw_audio)

    return ulaw_audio, pcm16_audio
