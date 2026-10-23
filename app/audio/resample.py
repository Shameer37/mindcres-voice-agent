
"""Audio resampling utilities for the SkyFiber voice agent."""

import numpy as np
from scipy.signal import resample_poly


def resample_pcm16(
    pcm16: bytes,
    *,
    original_rate: int,
    target_rate: int,
) -> bytes:
    """Resample little-endian signed PCM16 audio to a new sample rate."""
    if original_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive")

    if len(pcm16) % 2 != 0:
        raise ValueError("PCM16 audio must contain complete 2-byte samples")

    if not pcm16:
        return b""

    if original_rate == target_rate:
        return pcm16

    # Convert little-endian PCM16 bytes into normalized NumPy samples.
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float64)

    # Reduce the rate ratio to its simplest integer fraction.
    from math import gcd

    divisor = gcd(original_rate, target_rate)
    up = target_rate // divisor
    down = original_rate // divisor

    # Resample while applying anti-aliasing filtering.
    resampled = resample_poly(samples, up, down)

    # Convert back to signed 16-bit PCM, preventing overflow.
    resampled = np.clip(np.rint(resampled), -32768, 32767)
    return resampled.astype("<i2").tobytes()
