import asyncio

import pytest

from app.services import tts_service as tts_module
from app.services.tts_service import TTSService


@pytest.mark.asyncio
async def test_edge_provider_timeout_is_reported(monkeypatch):
    class SlowCommunicate:
        def __init__(self, **_kwargs):
            pass

        async def stream(self):
            await asyncio.sleep(1)
            yield {"type": "audio", "data": b"audio"}

    monkeypatch.setattr(tts_module.edge_tts, "Communicate", SlowCommunicate)
    service = TTSService()
    service.timeout_seconds = 0.01

    with pytest.raises(RuntimeError, match="provider timed out"):
        await service.synthesize_pcm16("hello")


@pytest.mark.asyncio
async def test_tts_final_partial_frame_uses_ulaw_silence(monkeypatch):
    service = TTSService()

    async def synthesize(_text, language="en"):
        return b"\x7f" * 161

    monkeypatch.setattr(service, "synthesize_ulaw", synthesize)
    frames = await service.synthesize_frames("hello")

    assert [len(frame) for frame in frames] == [160, 160]
    assert frames[-1][1:] == b"\xff" * 159


@pytest.mark.asyncio
async def test_tts_frame_cache_reuses_identical_prompt_and_voice(monkeypatch):
    service = TTSService()
    calls = 0

    async def synthesize(_text, language="en"):
        nonlocal calls
        calls += 1
        return b"\x7f" * 320

    monkeypatch.setattr(service, "synthesize_ulaw", synthesize)
    first = await service.synthesize_frames("What score from 1 to 5 would you give?")
    second = await service.synthesize_frames("What score from 1 to 5 would you give?")

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_tts_frame_cache_keeps_voice_language_separate(monkeypatch):
    service = TTSService()
    calls = 0

    async def synthesize(_text, language="en"):
        nonlocal calls
        calls += 1
        return b"\x7f" * 160

    monkeypatch.setattr(service, "synthesize_ulaw", synthesize)
    await service.synthesize_frames("Thank you.", language="en")
    await service.synthesize_frames("Thank you.", language="hi")

    assert calls == 2


def test_language_voice_names_are_explicit_and_supported():
    assert tts_module.LANGUAGE_VOICES == {
        "en": "en-US-AriaNeural",
        "hi": "hi-IN-SwaraNeural",
        "te": "te-IN-ShrutiNeural",
    }
