"""Controlled one-factor faster-whisper comparison on one shared synthetic corpus."""
import argparse
import asyncio
import csv
import ctypes
import gc
import json
import re
import subprocess
import sys
from pathlib import Path
from statistics import median
from time import process_time

import edge_tts
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.audio.resample import resample_pcm16
from app.audio.ulaw import encode_pcm16_to_ulaw
from app.services.stt_service import STTService
from app.services.tts_service import TTSService

PHRASES = [
    "Yeah.", "No.", "Fully resolved.", "Not resolved.", "Low internet.",
    "My internet is slow.", "The internet keeps dropping.",
    "The technician arrived late.", "I would rate the technician four.",
    "Eight.", "Nine.", "Nothing else to add.",
]
CONFIGS = ["baseline", "beam3", "auto_language", "condition_context", "vad_filter", "base_model"]


async def synthesize(tts, text):
    stream = edge_tts.Communicate(text=text, voice=tts.voice)
    chunks = [item["data"] async for item in stream.stream() if item.get("type") == "audio"]
    encoded = b"".join(chunks)
    cmd = [tts.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "pipe:1"]
    result = await asyncio.to_thread(subprocess.run, cmd, input=encoded, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode or not result.stdout:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout[:len(result.stdout) & ~1]


def words(value):
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    number_words = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
    return [number_words.get(token, token) for token in tokens]


def working_set_mb():
    """Current process working set on Windows; None on unsupported systems."""
    if sys.platform != "win32":
        return None
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    ok = get_memory(get_process(), ctypes.byref(counters), counters.cb)
    return round(counters.WorkingSetSize / (1024 * 1024), 1) if ok else None


def wer(reference, hypothesis):
    a, b = words(reference), words(hypothesis)
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = nxt
    return row[-1] / max(1, len(a))


def meaning_preserved(reference, hypothesis):
    """Small phrase-intent check for this fixed 12-phrase corpus, not a general evaluator."""
    ref, hyp = words(reference), words(hypothesis)
    have = set(hyp)
    if ref == ["yeah"]:
        return bool(have & {"yeah", "yes"})
    if ref == ["no"]:
        return bool(have & {"no", "nope"})
    if "fully" in ref and "resolved" in ref:
        return "resolved" in have and "not" not in have
    if "not" in ref and "resolved" in ref:
        return "not" in have and "resolved" in have
    if "low" in ref and "internet" in ref:
        return "internet" in have and bool(have & {"low", "poor"})
    if "slow" in ref:
        return "internet" in have and "slow" in have
    if "dropping" in ref:
        return "internet" in have and "dropping" in have
    if "technician" in ref and "late" in ref:
        return "technician" in have and "late" in have
    if "4" in ref:
        return "4" in have and "technician" in have
    if ref == ["8"]:
        return "8" in have
    if ref == ["9"]:
        return "9" in have
    if "nothing" in ref and "add" in ref:
        return "nothing" in have or ("no" in have and bool(have & {"other", "more", "feedback"}))
    return False


async def run(output, selected):
    sources = []
    tts = TTSService()
    for i, reference in enumerate(PHRASES, 1):
        pcm16 = await synthesize(tts, reference)
        pcm8 = resample_pcm16(pcm16, original_rate=16000, target_rate=8000)
        sources.append((i, reference, encode_pcm16_to_ulaw(pcm8)))

    rows = []
    for experiment in selected:
        service = STTService(experiment=experiment)
        for sample_id, reference, audio in sources:
            cpu_start = process_time()
            result = service.transcribe_ulaw(audio, language="en")
            rows.append({
                "experiment": experiment, "model": result["model_size"], "sample_id": sample_id,
                "reference": reference, "transcript": result["text"],
                "wer": round(wer(reference, result["text"]), 4),
                "exact_match": str(words(reference) == words(result["text"])).lower(),
                "meaning_preserved": str(meaning_preserved(reference, result["text"])).lower(),
                "stt_ms": round(result["stt_ms"], 1),
                "cpu_ms": round((process_time() - cpu_start) * 1000, 1),
                "audio_seconds": round(len(audio) / 8000, 2),
                "language_probability": round(result["language_probability"], 4),
                "working_set_mb": working_set_mb(),
            })
            print(json.dumps(rows[-1], ensure_ascii=False))
        del service
        gc.collect()

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for config in selected:
        group = [r for r in rows if r["experiment"] == config]
        times = sorted(r["stt_ms"] for r in group)
        summary.append({
            "experiment": config, "model": group[0]["model"], "n": len(group),
            "mean_wer": round(sum(r["wer"] for r in group) / len(group), 4),
            "exact_match_pct": round(100 * sum(r["exact_match"] == "true" for r in group) / len(group), 1),
            "median_stt_ms": round(median(times), 1), "p90_stt_ms": times[(9 * len(times) + 9) // 10 - 1],
            "median_cpu_ms": round(median(sorted(r["cpu_ms"] for r in group)), 1),
            "max_working_set_mb": max((r["working_set_mb"] or 0) for r in group) or None,
        })
    print(json.dumps({"output": str(output), "dataset": "Edge TTS synthetic English", "summary": summary}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "stt_experiment_results.csv")
    parser.add_argument("--configs", nargs="+", choices=CONFIGS, default=CONFIGS)
    parser.add_argument("--rescore-existing", action="store_true", help="Recompute fixed-corpus lexical/intent scores without model calls")
    args = parser.parse_args()
    if args.rescore_existing:
        with args.output.open("r", encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.DictReader(fp))
        for row in rows:
            row["wer"] = round(wer(row["reference"], row["transcript"]), 4)
            row["exact_match"] = str(words(row["reference"]) == words(row["transcript"])).lower()
            row["meaning_preserved"] = str(meaning_preserved(row["reference"], row["transcript"])).lower()
        with args.output.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps({"rescored": str(args.output), "samples": len(rows)}))
        return
    asyncio.run(run(args.output, args.configs))


if __name__ == "__main__":
    main()
