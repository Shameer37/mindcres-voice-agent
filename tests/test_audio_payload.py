
import base64
import struct

import pytest

from app.audio.payload import AudioPayloadError, decode_audio_payload


def test_decodes_valid_base64_ulaw_audio():
    # Three μ-law silence bytes.
    ulaw_audio = bytes([0xFF, 0xFF, 0xFF])
    payload = base64.b64encode(ulaw_audio).decode("ascii")

    decoded_ulaw, pcm16 = decode_audio_payload(payload)

    assert decoded_ulaw == ulaw_audio
    assert len(pcm16) == 6
    assert list(struct.iter_unpack("<h", pcm16)) == [(0,), (0,), (0,)]


def test_rejects_invalid_base64():
    with pytest.raises(AudioPayloadError, match="valid Base64"):
        decode_audio_payload("not-valid-base64!!!")


def test_rejects_empty_payload():
    with pytest.raises(AudioPayloadError, match="cannot be empty"):
        decode_audio_payload("")


def test_rejects_base64_that_decodes_to_empty_audio():
    payload = base64.b64encode(b"").decode("ascii")

    with pytest.raises(AudioPayloadError, match="cannot be empty"):
        decode_audio_payload(payload)


def test_rejects_non_string_payload():
    with pytest.raises(AudioPayloadError, match="Base64 string"):
        decode_audio_payload(None)


def test_pcm16_output_has_twice_the_input_byte_length():
    ulaw_audio = bytes([0x00, 0x7F, 0x80, 0xFF])
    payload = base64.b64encode(ulaw_audio).decode("ascii")

    decoded_ulaw, pcm16 = decode_audio_payload(payload)

    assert len(decoded_ulaw) == 4
    assert len(pcm16) == 8
