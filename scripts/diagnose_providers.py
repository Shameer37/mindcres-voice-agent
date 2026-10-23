"""Check local STT cache, Ollama availability, and Edge TTS reachability."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

async def check_ollama() -> bool:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            names = {item.get("name", "") for item in response.json().get("models", [])}
        found = any(name == model or name.startswith(model + ":") for name in names)
        print(f"LLM: {'READY' if found else 'MODEL MISSING'} ({model}, {base_url})")
        return found
    except Exception as exc:
        print(f"LLM: UNAVAILABLE ({type(exc).__name__}: {exc})")
        return False


async def check_tts() -> bool:
    try:
        from app.services.tts_service import TTSService

        service = TTSService()
        frames = await asyncio.wait_for(
            service.synthesize_frames("This is a SkyFiber speech test."),
            timeout=30,
        )
        valid = bool(frames) and all(len(frame) == 160 for frame in frames)
        print(f"TTS: {'READY' if valid else 'INVALID AUDIO'} (Edge TTS, {len(frames)} frames)")
        return valid
    except Exception as exc:
        print(f"TTS: UNAVAILABLE ({type(exc).__name__}: {exc})")
        return False


def check_stt_cache(model: str) -> bool:
    try:
        local_model = Path(model)
        if local_model.is_dir():
            weights = local_model / "model.bin"
        else:
            cache_root = Path(
                os.getenv(
                    "HF_HUB_CACHE",
                    str(Path.home() / ".cache" / "huggingface" / "hub"),
                )
            )
            snapshots = cache_root / f"models--Systran--faster-whisper-{model}" / "snapshots"
            weights = next(snapshots.glob("*/model.bin"), None) if snapshots.is_dir() else None
        stt_ok = weights is not None and Path(weights).is_file()
        if stt_ok:
            print(
                f"STT: CACHE FILE FOUND (faster-whisper {model}); "
                "model load/inference not tested by this quick diagnostic."
            )
        else:
            print(
                f"STT: MODEL NOT CACHED ({model}). "
                "Run `python scripts/download_stt_model.py` explicitly."
            )
    except Exception as exc:
        print(
            f"STT: CACHE CHECK FAILED ({type(exc).__name__}: {exc}); "
            "model load/inference not tested."
        )
        stt_ok = False

    return stt_ok


async def check_other_providers() -> bool:
    llm_ok, tts_ok = await asyncio.gather(check_ollama(), check_tts())
    return llm_ok and tts_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stt-only", action="store_true")
    args = parser.parse_args()

    stt_ok = check_stt_cache(os.getenv("STT_MODEL_SIZE", "tiny"))
    if args.stt_only:
        # This path is synchronous and does not start an asyncio loop, so it
        # also works in restricted Windows environments without loopback.
        return 0 if stt_ok else 1

    other_ok = asyncio.run(check_other_providers())
    return 0 if stt_ok and other_ok else 1


if __name__ == "__main__":
    sys.exit(main())
