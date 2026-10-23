
import numpy as np
import pytest

from app.audio.resample import resample_pcm16


def test_resample_8khz_to_16khz_doubles_sample_count():
    samples = np.array([0, 1000, -1000, 2000], dtype="<i2")
    result = resample_pcm16(
        samples.tobytes(),
        original_rate=8000,
        target_rate=16000,
    )

    assert len(result) == len(samples) * 2 * 2


def test_same_sample_rate_returns_original_audio():
    audio = np.array([100, -100], dtype="<i2").tobytes()

    assert resample_pcm16(
        audio,
        original_rate=8000,
        target_rate=8000,
    ) == audio


def test_empty_audio_returns_empty_bytes():
    assert resample_pcm16(
        b"",
        original_rate=8000,
        target_rate=16000,
    ) == b""


def test_odd_length_pcm16_raises_error():
    with pytest.raises(ValueError):
        resample_pcm16(
            b"\x01",
            original_rate=8000,
            target_rate=16000,
        )


def test_invalid_sample_rate_raises_error():
    with pytest.raises(ValueError):
        resample_pcm16(
            b"\x00\x00",
            original_rate=0,
            target_rate=16000,
        )
