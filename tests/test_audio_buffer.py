
import pytest

from app.audio.buffer import AudioFrameBuffer
from app.audio.ulaw import ULAW_FRAME_BYTES


def test_default_frame_size_is_160_bytes():
    buffer = AudioFrameBuffer()

    assert buffer.frame_size == 160


def test_incomplete_audio_is_buffered():
    buffer = AudioFrameBuffer()

    frames = buffer.add_audio(b"\x01" * 100)

    assert frames == []
    assert buffer.pending_bytes == 100


def test_two_chunks_produce_one_complete_frame():
    buffer = AudioFrameBuffer()

    first_frames = buffer.add_audio(b"\x01" * 100)
    second_frames = buffer.add_audio(b"\x02" * 60)

    assert first_frames == []
    assert len(second_frames) == 1
    assert second_frames[0] == b"\x01" * 100 + b"\x02" * 60
    assert buffer.pending_bytes == 0


def test_multiple_frames_from_one_chunk():
    buffer = AudioFrameBuffer()
    audio = b"\x03" * (ULAW_FRAME_BYTES * 3)

    frames = buffer.add_audio(audio)

    assert len(frames) == 3
    assert all(len(frame) == ULAW_FRAME_BYTES for frame in frames)
    assert buffer.pending_bytes == 0


def test_remainder_is_preserved_after_complete_frames():
    buffer = AudioFrameBuffer()
    audio = b"\x04" * (ULAW_FRAME_BYTES + 25)

    frames = buffer.add_audio(audio)

    assert len(frames) == 1
    assert buffer.pending_bytes == 25


def test_empty_audio_does_not_change_buffer():
    buffer = AudioFrameBuffer()
    buffer.add_audio(b"\x05" * 20)

    assert buffer.add_audio(b"") == []
    assert buffer.pending_bytes == 20


def test_reset_discards_pending_bytes():
    buffer = AudioFrameBuffer()
    buffer.add_audio(b"\x06" * 50)

    buffer.reset()

    assert buffer.pending_bytes == 0
    assert buffer.add_audio(b"\x07" * 100) == []


def test_custom_frame_size():
    buffer = AudioFrameBuffer(frame_size=4)

    frames = buffer.add_audio(b"abcdefghij")

    assert frames == [b"abcd", b"efgh"]
    assert buffer.pending_bytes == 2


def test_frame_size_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        AudioFrameBuffer(frame_size=0)


def test_audio_must_be_bytes():
    buffer = AudioFrameBuffer()

    with pytest.raises(TypeError, match="bytes"):
        buffer.add_audio("not bytes")
