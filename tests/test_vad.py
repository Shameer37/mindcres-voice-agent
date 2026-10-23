
import struct

import numpy as np
import pytest

from app.audio.vad import EnergyVAD, SustainedSpeechGate


def test_silence_has_zero_rms():
    vad = EnergyVAD()
    silence = struct.pack("<160h", *([0] * 160))

    assert vad.calculate_rms(silence) == 0.0
    assert vad.is_speech(silence) is False


def test_loud_frame_is_speech():
    vad = EnergyVAD(threshold=500)
    loud_frame = struct.pack("<160h", *([1000] * 160))

    assert vad.calculate_rms(loud_frame) == 1000.0
    assert vad.is_speech(loud_frame) is True


def test_quiet_frame_is_silence():
    vad = EnergyVAD(threshold=500)
    quiet_frame = struct.pack("<160h", *([100] * 160))

    assert vad.is_speech(quiet_frame) is False


def test_empty_audio_has_zero_rms():
    assert EnergyVAD().calculate_rms(b"") == 0.0


def test_odd_length_pcm_is_rejected():
    with pytest.raises(ValueError):
        EnergyVAD().calculate_rms(b"\x00")


def test_negative_threshold_is_rejected():
    with pytest.raises(ValueError):
        EnergyVAD(threshold=-1)


def test_speech_like_rejects_sustained_broadband_noise():
    rng = np.random.default_rng(42)
    vad = EnergyVAD(threshold=500)
    frames = [
        (rng.normal(0, 1400, 160).clip(-32768, 32767)).astype("<i2").tobytes()
        for _ in range(20)
    ]

    assert all(vad.calculate_rms(frame) >= 500 for frame in frames)
    assert sum(vad.is_speech_like(frame) for frame in frames) == 0


def test_speech_like_accepts_voiced_caller_frames():
    vad = EnergyVAD(threshold=500)
    samples = (
        1800 * np.sin(2 * np.pi * 120 * np.arange(160) / 8000)
    ).astype("<i2")

    assert vad.is_speech_like(samples.tobytes())


def test_noise_energy_does_not_count_as_speech_for_response_timing():
    rng = np.random.default_rng(7)
    vad = EnergyVAD(threshold=500)
    noise_frame = rng.normal(0, 1000, 160).astype("<i2").tobytes()

    # This is the same predicate used for both turn boundaries and barge-in.
    assert vad.calculate_rms(noise_frame) >= 500
    assert not vad.is_speech_like(noise_frame)


def test_sustained_speech_gate_rejects_short_burst_and_keeps_onset_frames():
    gate = SustainedSpeechGate(min_speech_ms=60, max_gap_ms=40)
    onset = [bytes([n]) * 160 for n in range(1, 7)]

    assert gate.push(onset[0], True) is None
    assert gate.push(onset[1], False) is None
    assert gate.push(onset[2], True) is None
    triggered = gate.push(onset[3], True)
    assert triggered == tuple(onset[:4])

    assert gate.push(onset[4], True) is None
    assert gate.push(onset[5], False) is None
    assert gate.push(b"\x00" * 160, False) is None
    assert gate.push(b"\x00" * 160, False) is None
    assert gate.frames == []


def test_sustained_speech_gate_does_not_trigger_on_separated_short_bursts():
    gate = SustainedSpeechGate(min_speech_ms=60, max_gap_ms=20)
    voice = b"\x01" * 160
    quiet = b"\xff" * 160

    assert gate.push(voice, True) is None
    assert gate.push(quiet, False) is None
    assert gate.push(quiet, False) is None  # exceeds the allowed gap
    assert gate.push(voice, True) is None
    assert gate.push(voice, True) is None
