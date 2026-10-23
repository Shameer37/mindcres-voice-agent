"""Run a real-time scripted caller against the running SkyFiber API.

This is an integration client: it requires the API, a cached faster-whisper
model, Ollama for flexible extraction/replies, and outbound Edge TTS access.
The caller side uses Edge TTS too. It does not mock recognition or results.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import datetime, timezone
from collections import deque
from pathlib import Path

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.audio.ulaw import split_ulaw_frames
from app.services.tts_service import TTSService


SCENARIOS = {
    "happy": [
        "Yes, I am the account holder.",
        "Yes, the issue is fixed, there are no problems, and the technician came on time.",
        "I would rate the technician five out of five.",
        "I would recommend SkyFiber nine out of ten.",
        "No other feedback, thank you.",
    ],
    "unresolved": [
        "Yes, I am the account holder.",
        "No, the issue is not resolved.",
        "My internet keeps dropping.",
        "No, the technician did not arrive on time.",
        "I would rate the technician three out of five.",
        "I would recommend SkyFiber four out of ten.",
        "No other feedback.",
    ],
    "barge-in": [
        "Yes, I am the account holder.",
        "Yes, the issue is fixed, there are no problems, and the technician came on time.",
        "I would rate the technician five out of five.",
        "I would recommend SkyFiber nine out of ten.",
        "No other feedback.",
    ],
    "wrong-person": ["No, I am not the account holder."],
}

FRAME_BYTES = 160
FRAME_SECONDS = 0.020
SILENCE_FRAMES = 45  # 900 ms, above the configured default turn threshold


def wall_time() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def event_type(message: dict) -> str | None:
    return message.get("event") or message.get("type")


async def synthesize_phrases(phrases: list[str]) -> list[list[bytes]]:
    service = TTSService()
    result = []
    for phrase in phrases:
        ulaw = await service.synthesize_ulaw(phrase)
        frames = list(split_ulaw_frames(ulaw, FRAME_BYTES, pad_final=True))
        if not frames:
            raise RuntimeError(f"Caller TTS produced no frames for: {phrase}")
        result.append(frames)
    return result


async def stream_frames(ws, frames: list[bytes]) -> None:
    started = time.perf_counter()
    sequence = frames + [b"\xff" * FRAME_BYTES] * SILENCE_FRAMES
    for index, frame in enumerate(sequence, start=1):
        await ws.send(json.dumps({
            "event": "media",
            "payload": base64.b64encode(frame).decode("ascii"),
        }))
        target = index * FRAME_SECONDS
        delay = target - (time.perf_counter() - started)
        if delay > 0:
            await asyncio.sleep(delay)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--customer-id", default="CUST001")
    parser.add_argument("--scenario", choices=SCENARIOS, default="happy")
    parser.add_argument(
        "--barge-after-seconds", type=float, default=0.55,
        help="For barge-in, begin the next answer this long after the bot transcript.",
    )
    args = parser.parse_args()

    phrases = SCENARIOS[args.scenario]
    print("Generating caller speech before connecting (Edge TTS).")
    try:
        phrase_frames = await synthesize_phrases(phrases)
    except Exception as exc:
        print(f"CALLER TTS UNAVAILABLE: {type(exc).__name__}: {exc}")
        return 2

    base_url = args.base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{base_url}/calls", json={"customer_id": args.customer_id}
        )
        response.raise_for_status()
        call_id = response.json()["id"]

    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url += f"/ws/call/{call_id}"
    pending: deque[dict] = deque()
    reader_error: list[BaseException] = []
    reader_closed = asyncio.Event()
    media_frame_count = 0
    reply_media_frames: dict[int, int] = {}
    media_frame_received = asyncio.Event()
    bot_reply_sequence = 0
    current_bot_reply_sequence = 0
    hangup_seen = False

    async with websockets.connect(
        ws_url,
        open_timeout=10,
        close_timeout=5,
        max_queue=None,
        ping_interval=None,
    ) as ws:
        async def reader() -> None:
            nonlocal hangup_seen, media_frame_count
            nonlocal bot_reply_sequence, current_bot_reply_sequence
            try:
                async for raw in ws:
                    message = json.loads(raw)
                    kind = event_type(message)
                    if kind == "hangup":
                        hangup_seen = True
                    if kind == "media":
                        media_frame_count += 1
                        reply_media_frames[current_bot_reply_sequence] = (
                            reply_media_frames.get(current_bot_reply_sequence, 0) + 1
                        )
                        media_frame_received.set()
                    if kind == "transcript" and message.get("role") == "bot":
                        bot_reply_sequence += 1
                        current_bot_reply_sequence = bot_reply_sequence
                    if kind == "mark":
                        # The test client represents playback: the server has
                        # paced each 20 ms frame, then this brief drain matches
                        # one queued 20 ms frame before acknowledging the mark.
                        await asyncio.sleep(FRAME_SECONDS)
                        await ws.send(json.dumps({
                            "event": "mark",
                            "name": (message.get("mark") or {}).get("name", ""),
                        }))
                        print(f"MARK ACKED at={wall_time()} sequence={current_bot_reply_sequence}")
                        message["_bot_reply_sequence"] = current_bot_reply_sequence
                    pending.append(message)
            except websockets.ConnectionClosedOK:
                reader_closed.set()
            except BaseException as exc:
                reader_error.append(exc)

        reader_task = asyncio.create_task(reader())

        async def next_event(timeout: float = 60) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if pending:
                    return pending.popleft()
                if reader_error:
                    raise RuntimeError(f"WebSocket reader stopped: {reader_error[0]}")
                if reader_closed.is_set():
                    raise EOFError("WebSocket closed normally")
                await asyncio.sleep(0.01)
            raise TimeoutError("Timed out waiting for server event")

        async def wait_for(predicate, timeout: float = 60) -> dict:
            while True:
                message = await next_event(timeout)
                kind = event_type(message)
                if kind == "error":
                    raise RuntimeError(message.get("message", "server error"))
                if kind == "hangup":
                    return message
                if predicate(message):
                    if kind == "mark":
                        # Let the server's receive loop observe the ACK and
                        # finish its playback task before the next caller turn.
                        await asyncio.sleep(0.15)
                    return message

        try:
            reply_baseline = bot_reply_sequence
            ready = await wait_for(lambda item: event_type(item) == "ready")
            print(f"CONNECTED call_id={call_id}: {ready.get('message', '')}")
            await ws.send(json.dumps({
                "event": "start", "call_id": call_id,
                "customer_id": args.customer_id,
            }))
            await wait_for(
                lambda item: event_type(item) == "mark"
                and item.get("_bot_reply_sequence", 0) > reply_baseline
            )

            index = 0
            while index < len(phrase_frames):
                reply_baseline = bot_reply_sequence
                print(f"CALLER at={wall_time()}: {phrases[index]}")
                await stream_frames(ws, phrase_frames[index])
                index += 1
                if index >= len(phrase_frames):
                    # Continue draining until the final bot reply/hangup.
                    break

                interrupted_next_sent = False
                barge_reply_seen = False
                while True:
                    message = await next_event(timeout=60)
                    kind = event_type(message)
                    if kind == "error":
                        raise RuntimeError(message.get("message", "server error"))
                    if kind == "hangup":
                        index = len(phrase_frames)
                        break
                    if kind == "transcript" and message.get("role") == "bot":
                        print(f"AGENT: {message.get('text', '')}")
                        if interrupted_next_sent:
                            barge_reply_seen = True
                        if args.scenario == "barge-in" and index == 1 and not interrupted_next_sent:
                            target_reply_sequence = current_bot_reply_sequence
                            target_frame_count = (
                                reply_media_frames.get(target_reply_sequence, 0) + 5
                            )
                            while reply_media_frames.get(target_reply_sequence, 0) < target_frame_count:
                                media_frame_received.clear()
                                if reply_media_frames.get(target_reply_sequence, 0) >= target_frame_count:
                                    break
                                await asyncio.wait_for(
                                    media_frame_received.wait(), timeout=10
                                )
                            await asyncio.sleep(args.barge_after_seconds)
                            print(f"BARGE-IN CALLER at={wall_time()}: {phrases[index]}")
                            reply_baseline = bot_reply_sequence
                            # The server may finish processing this answer
                            # before the last caller audio frame is sent. Set
                            # the flag first so the reader can recognize that
                            # early bot reply instead of waiting on its timer
                            # reminder and mistaking that mark for the reply.
                            interrupted_next_sent = True
                            await stream_frames(ws, phrase_frames[index])
                            index += 1
                    if kind == "mark":
                        if interrupted_next_sent and not barge_reply_seen:
                            continue
                        if (
                            index < len(phrase_frames)
                            and message.get("_bot_reply_sequence", 0)
                            > reply_baseline
                        ):
                            await asyncio.sleep(0.15)
                            break
                    if kind == "clear":
                        print("BARGE-IN: server cleared queued agent audio")

            try:
                while True:
                    message = await next_event(timeout=30)
                    if event_type(message) == "transcript" and message.get("role") == "bot":
                        print(f"AGENT: {message.get('text', '')}")
                    if event_type(message) == "hangup":
                        print(f"HANGUP: {message.get('reason', 'completed')}")
                        break
            except (TimeoutError, EOFError):
                # The server may already have closed after persisting its
                # terminal result. Never turn that normal close into a client
                # failure while cleaning up the reader.
                try:
                    await ws.send(json.dumps({"event": "hangup"}))
                except websockets.ConnectionClosed:
                    pass
        finally:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)

    async with httpx.AsyncClient(timeout=10) as client:
        result_response = await client.get(f"{base_url}/calls/{call_id}")
        if result_response.is_success:
            saved = result_response.json()
            result = saved.get("result") or {}
            answers = result.get("answers") or {}
            print("SAVED RESULT:")
            print(json.dumps(saved, indent=2))

            expected = {
                "happy": {
                    "outcome": "completed",
                    "identity_confirmed": True,
                    "issue_resolved": "yes",
                    "technician_on_time": True,
                    "technician_rating": 5,
                    "nps": 9,
                },
                "unresolved": {
                    "outcome": "completed",
                    "end_reason": "needs_followup",
                    "identity_confirmed": True,
                    "issue_resolved": "no",
                    "technician_rating": 3,
                    "nps": 4,
                    "needs_followup": True,
                    "still_facing": "My connection still keeps dropping.",
                },
                "barge-in": {
                    "outcome": "completed",
                    "identity_confirmed": True,
                    "issue_resolved": "yes",
                    "technician_rating": 5,
                    "nps": 9,
                },
                "wrong-person": {
                    "outcome": "wrong_person",
                    "identity_confirmed": False,
                },
            }[args.scenario]
            failures = [
                f"{key}: expected {value!r}, got {(answers if key in answers else result).get(key)!r}"
                for key, value in expected.items()
                if (
                    not bool((answers if key in answers else result).get(key))
                    if key == "still_facing"
                    else (answers if key in answers else result).get(key) != value
                )
            ]
            if args.scenario == "barge-in":
                interruptions = [
                    metric for metric in saved.get("turn_metrics", [])
                    if metric.get("interrupted_at_ms") is not None
                ]
                if not interruptions:
                    failures.append("no interrupted_at_ms metric was saved")
                elif not any(
                    metric.get("tts_first_audio_ms") is not None
                    and metric["interrupted_at_ms"] > metric["tts_first_audio_ms"]
                    for metric in interruptions
                ):
                    failures.append(
                        "interruption did not occur after a first agent audio frame"
                    )
            if failures:
                print("SCENARIO FAIL:")
                for failure in failures:
                    print(f"- {failure}")
                return 1
            if not hangup_seen:
                print("SCENARIO FAIL: server closed without a hangup event")
                return 1
            print("SCENARIO PASS: saved result matched all assertions")
        else:
            print(f"Could not retrieve saved call: HTTP {result_response.status_code}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
