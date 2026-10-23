
import asyncio
import sys
from pathlib import Path

# Add the project root so Python can import the app package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.tts_service import TTSService


async def main():
    tts = TTSService()

    text = (
        "Hello, I'm calling from SkyFiber Broadband. "
        "Am I speaking with the account holder?"
    )

    frames = await tts.synthesize_frames(text)

    print(f"Generated frames: {len(frames)}")
    print(f"First frame size: {len(frames[0]) if frames else 0} bytes")
    print("Expected frame size: 160 bytes")
    print(f"Audio duration: approximately {len(frames) * 0.02:.2f} seconds")

    assert frames, "TTS generated no audio."
    assert all(len(frame) == 160 for frame in frames), (
        "Every μ-law frame must contain exactly 160 bytes."
    )

    print("TTS test passed.")


if __name__ == "__main__":
    asyncio.run(main())
