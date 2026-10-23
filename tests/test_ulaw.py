
import struct

import pytest

from app.audio.ulaw import (
    ULAW_FRAME_BYTES,
    decode_sample,
    decode_ulaw_to_pcm16,
    encode_pcm16_to_ulaw,
    encode_sample,
    split_ulaw_frames,
)


def make_pcm16(*samples: int) -> bytes:
    return b"".join(struct.pack("<h", sample) for sample in samples)


def test_encode_and_decode_silence():
    encoded = encode_pcm16_to_ulaw(make_pcm16(0, 0, 0))
    assert encoded == bytes([0xFF, 0xFF, 0xFF])

    decoded = decode_ulaw_to_pcm16(encoded)
    assert len(decoded) == 6
    assert all(sample == 0 for (sample,) in struct.iter_unpack("<h", decoded))


def test_zero_byte_is_loud_ulaw_not_silence():
    # Browser queue underruns must use 0xFF, never a zero-filled Uint8Array.
    assert decode_sample(0xFF) == 0
    assert abs(decode_sample(0x00)) > 32000


def test_pcm16_encoding_halves_byte_length():
    pcm = make_pcm16(-20000, -1000, 0, 1000, 20000)
    encoded = encode_pcm16_to_ulaw(pcm)

    assert len(pcm) == 10
    assert len(encoded) == 5


def test_ulaw_decoding_doubles_byte_length():
    encoded = bytes([0x00, 0x7F, 0x80, 0xFF])
    decoded = decode_ulaw_to_pcm16(encoded)

    assert len(encoded) == 4
    assert len(decoded) == 8


def test_sample_round_trip_preserves_sign():
    for sample in [-20000, -3000, -500, 500, 3000, 20000]:
        decoded = decode_sample(encode_sample(sample))
        assert (decoded < 0) == (sample < 0)


def test_encode_rejects_out_of_range_sample():
    with pytest.raises(ValueError):
        encode_sample(40000)


def test_encode_rejects_odd_pcm_byte_count():
    with pytest.raises(ValueError):
        encode_pcm16_to_ulaw(b"\x00")


def test_decode_accepts_all_ulaw_byte_values():
    decoded = decode_ulaw_to_pcm16(bytes(range(256)))
    assert len(decoded) == 512


def test_20ms_frame_is_160_bytes():
    audio = bytes(ULAW_FRAME_BYTES)
    frames = list(split_ulaw_frames(audio))

    assert len(frames) == 1
    assert len(frames[0]) == 160


def test_audio_splits_into_multiple_frames():
    audio = bytes(ULAW_FRAME_BYTES * 3)
    frames = list(split_ulaw_frames(audio))

    assert len(frames) == 3
    assert all(len(frame) == 160 for frame in frames)


def test_incomplete_final_frame_is_dropped_by_default():
    audio = bytes(ULAW_FRAME_BYTES + 20)
    frames = list(split_ulaw_frames(audio))

    assert len(frames) == 1


def test_incomplete_final_frame_can_be_padded():
    audio = bytes(ULAW_FRAME_BYTES + 20)
    frames = list(split_ulaw_frames(audio, pad_final=True))

    assert len(frames) == 2
    assert len(frames[-1]) == 160
    assert frames[-1][20:] == b"\xff" * 140


def test_padded_final_frame_is_silent_ulaw():
    frame = list(split_ulaw_frames(b"\x80", frame_size=160, pad_final=True))[0]
    pcm = decode_ulaw_to_pcm16(frame[1:])
    assert frame[1:] == b"\xff" * 159
    assert all(sample == 0 for (sample,) in struct.iter_unpack("<h", pcm))


def test_frame_size_must_be_positive():
    with pytest.raises(ValueError):
        list(split_ulaw_frames(b"audio", frame_size=0))
