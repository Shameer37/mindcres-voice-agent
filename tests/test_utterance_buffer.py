
import pytest

from app.audio.utterance_buffer import UtteranceBuffer


def test_add_and_get_audio():
    buffer = UtteranceBuffer()
    first_frame = b"\x11" * 160
    second_frame = b"\x22" * 160

    buffer.add_frame(first_frame)
    buffer.add_frame(second_frame)

    assert buffer.get_audio() == first_frame + second_frame
    assert len(buffer) == 320


def test_duration_is_calculated_correctly():
    buffer = UtteranceBuffer()
    buffer.add_frame(b"\xff" * 160)

    assert buffer.duration_seconds() == pytest.approx(0.02)


def test_clear_resets_buffer():
    buffer = UtteranceBuffer()
    buffer.add_frame(b"\xff" * 160)

    buffer.clear()

    assert len(buffer) == 0
    assert buffer.get_audio() == b""
    assert buffer.duration_seconds() == 0


def test_rejects_audio_exceeding_maximum_duration():
    buffer = UtteranceBuffer(max_duration_seconds=0.02)
    buffer.add_frame(b"\xff" * 160)

    with pytest.raises(ValueError, match="maximum duration"):
        buffer.add_frame(b"\xff")


def test_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        UtteranceBuffer(max_duration_seconds=0)

    with pytest.raises(ValueError):
        UtteranceBuffer(sample_rate=0)


def test_rejects_non_bytes_frames():
    buffer = UtteranceBuffer()

    with pytest.raises(TypeError):
        buffer.add_frame(bytearray(b"\xff" * 160))
