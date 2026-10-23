"""Replay checked-in WAV assets through the 8 kHz μ-law speech gate."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audio.resample import resample_pcm16
from app.audio.turn_detector import TurnDetector
from app.audio.ulaw import decode_ulaw_to_pcm16, encode_pcm16_to_ulaw
from app.audio.vad import EnergyVAD


def evaluate(path: Path, thresholds: tuple[int, ...]) -> dict:
    samples, sample_rate = sf.read(path, dtype="int16")
    if samples.ndim > 1:
        samples = samples.mean(axis=1).astype("int16")
    pcm16 = samples.tobytes()
    if sample_rate != 8000:
        pcm16 = resample_pcm16(
            pcm16, original_rate=sample_rate, target_rate=8000
        )
    ulaw = encode_pcm16_to_ulaw(pcm16)
    frames = [ulaw[i:i + 160] for i in range(0, len(ulaw) - 159, 160)]
    vad = EnergyVAD(threshold=500)
    speech = [
        vad.is_speech_like(decode_ulaw_to_pcm16(frame)) for frame in frames
    ]
    turns = {}
    for silence_ms in thresholds:
        detector = TurnDetector(
            silence_duration_ms=silence_ms, frame_duration_ms=20
        )
        count = 0
        for active in speech:
            count += detector.process_frame(active)
        for _ in range(60):
            count += detector.process_frame(False)
        turns[str(silence_ms)] = count
    return {
        "file": path.name,
        "duration_sec": round(len(frames) * 0.02, 2),
        "speech_like_frames": sum(speech),
        "frames": len(frames),
        "turns_by_silence_ms": turns,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--silence-ms", type=int, nargs="+", default=[500, 800, 1000]
    )
    args = parser.parse_args()
    for audio_path in sorted(args.audio_dir.glob("*.wav")):
        print(json.dumps(evaluate(audio_path, tuple(args.silence_ms))))


if __name__ == "__main__":
    main()
