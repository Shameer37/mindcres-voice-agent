
"""G.711 μ-law codec utilities for 8 kHz telephony audio."""

import struct
from collections.abc import Iterator

BIAS = 0x84
CLIP = 32635
ULAW_FRAME_BYTES = 160  # 20 ms at 8,000 samples/second




def encode_sample(sample: int) -> int:
    """Encode one signed 16-bit PCM sample into G.711 μ-law."""
    if not -32768 <= sample <= 32767:
        raise ValueError("PCM sample must be in the signed 16-bit range")

    sign = 0x80 if sample < 0 else 0

    # G.711 uses one's-complement-style handling for negative samples.
    magnitude = -sample if sample < 0 else sample
    magnitude = min(magnitude, CLIP)

    if sample < 0:
        magnitude = max(0, magnitude - 1)

    magnitude += BIAS

    exponent = 7
    mask = 0x4000

    while exponent > 0 and not (magnitude & mask):
        exponent -= 1
        mask >>= 1

    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF



def decode_sample(encoded: int) -> int:
    """Decode one μ-law byte into one signed 16-bit PCM sample."""
    if not 0 <= encoded <= 255:
        raise ValueError("μ-law sample must be in the byte range 0..255")

    value = (~encoded) & 0xFF
    magnitude = ((value & 0x0F) << 3) + BIAS
    magnitude <<= (value & 0x70) >> 4

    if value & 0x80:
        return BIAS - magnitude

    return magnitude - BIAS



def encode_pcm16_to_ulaw(pcm16: bytes) -> bytes:
    """Convert little-endian PCM16 audio to G.711 μ-law (numpy vectorised)."""
    if len(pcm16) % 2 != 0:
        raise ValueError("PCM16 input must contain complete 2-byte samples")

    import numpy as np

    BIAS_NP = np.int32(0x84)
    CLIP_NP = np.int32(32635)

    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.int32)

    sign = np.where(samples < 0, np.int32(0x80), np.int32(0))
    magnitude = np.abs(samples)
    magnitude = np.clip(magnitude, 0, CLIP_NP)
    # G.711 negative-sample correction (one's-complement style)
    magnitude = np.where(samples < 0, np.maximum(magnitude - 1, 0), magnitude)
    magnitude += BIAS_NP

    # Find the exponent (leading bit position in bits 14..0)
    exp = np.zeros(len(samples), dtype=np.int32)
    for e in range(7, 0, -1):
        exp = np.where((exp == 0) & ((magnitude & (1 << (e + 7))) != 0), e, exp)

    mantissa = (magnitude >> (exp + 3)) & np.int32(0x0F)
    encoded = (~(sign | (exp << 4) | mantissa)) & np.int32(0xFF)

    return encoded.astype(np.uint8).tobytes()



def decode_ulaw_to_pcm16(ulaw: bytes) -> bytes:
    """
    Convert μ-law bytes to little-endian signed 16-bit PCM bytes.

    Every input byte becomes 2 output bytes.
    """
    output = bytearray()

    for encoded in ulaw:
        sample = decode_sample(encoded)
        output.extend(struct.pack("<h", sample))

    return bytes(output)


def split_ulaw_frames(
    audio: bytes,
    frame_size: int = ULAW_FRAME_BYTES,
    *,
    pad_final: bool = False,
    pad_byte: int = 0xFF,
) -> Iterator[bytes]:
    """
    Split μ-law audio into fixed-size frames.

    Incomplete final audio is dropped by default. Set pad_final=True to pad
    the final frame. G.711 μ-law silence is 0xFF (zero bytes are loud audio).
    """
    if frame_size <= 0:
        raise ValueError("frame_size must be positive")
    if not 0 <= pad_byte <= 255:
        raise ValueError("pad_byte must be in the byte range 0..255")

    for offset in range(0, len(audio) - frame_size + 1, frame_size):
        yield audio[offset:offset + frame_size]

    remainder_start = (len(audio) // frame_size) * frame_size
    remainder = audio[remainder_start:]

    if remainder and pad_final:
        yield remainder + bytes([pad_byte]) * (frame_size - len(remainder))
