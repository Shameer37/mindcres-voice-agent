
import asyncio
import base64
import json
import time
from pathlib import Path

import numpy as np
import websockets
from scipy.io import wavfile

from app.audio.resample import resample_pcm16
from app.audio.ulaw import encode_pcm16_to_ulaw

SERVER_URL = "ws://127.0.0.1:8000"
WAV_PATH = Path("data/test_speech.wav")

FRAME_BYTES = 160
FRAME_SECONDS = 0.020
SILENCE_SECONDS = 0.8
ACK_TIMEOUT_SECONDS = 60


def load_wav_as_ulaw(path: Path) -> bytes:
    sample_rate, audio = wavfile.read(path)

    if audio.ndim == 2:
        audio = audio.astype(np.float64).mean(axis=1)

    if audio.dtype != np.int16:
        if np.issubdtype(audio.dtype, np.floating):
            raise ValueError(
                "Expected a 16-bit PCM WAV file. "
                "Export your recording as 16-bit PCM."
            )
        audio = np.clip(audio, -32768, 32767).astype(np.int16)

    audio = np.asarray(audio, dtype="<i2")
    pcm16 = audio.tobytes()

    pcm16_8k = resample_pcm16(
        pcm16,
        original_rate=sample_rate,
        target_rate=8000,
    )
    ulaw = encode_pcm16_to_ulaw(pcm16_8k)

    remainder = len(ulaw) % FRAME_BYTES
    if remainder:
        ulaw += b"\xff" * (FRAME_BYTES - remainder)

    silence_frames = round(SILENCE_SECONDS / FRAME_SECONDS)
    ulaw += b"\xff" * (FRAME_BYTES * silence_frames)

    print(f"Recording: {path}")
    print(f"Original sample rate: {sample_rate} Hz")
    print(f"Original duration: {len(audio) / sample_rate:.2f} seconds")
    print(f"Stream duration: {len(ulaw) / 8000:.2f} seconds")

    return ulaw


def print_server_event(event: dict) -> None:
    event_type = event.get("type")

    if event_type == "transcript":
        role = event.get("role", "unknown").upper()
        print(f"\n[{role}] {event.get('text', '')}")

    elif event_type == "error":
        print(f"\n[SERVER ERROR] {event.get('message', event)}")

    elif event_type == "ready":
        print("\n[CONNECTED] WebSocket is ready.")

    elif event_type == "turn_complete":
        print("\n[TURN COMPLETE]")

    elif event_type == "hangup":
        print("\n[CALL ENDED]")

    # Audio frames and ACKs are handled separately.


async def receive_until_media_ack(websocket) -> None:
    while True:
        raw = await asyncio.wait_for(
            websocket.recv(),
            timeout=ACK_TIMEOUT_SECONDS,
        )
        event = json.loads(raw)
        print_server_event(event)

        if event.get("type") == "ack" and event.get("event") == "media":
            return

        if event.get("type") == "error":
            continue

        if event.get("type") == "hangup":
            raise RuntimeError("The server ended the call.")


async def stream_recording(websocket, path: Path) -> None:
    ulaw_audio = load_wav_as_ulaw(path)
    frames = [
        ulaw_audio[i:i + FRAME_BYTES]
        for i in range(0, len(ulaw_audio), FRAME_BYTES)
    ]

    print(f"\nStreaming {len(frames)} frames...")

    started = time.perf_counter()

    for index, frame in enumerate(frames, start=1):
        await websocket.send(json.dumps({
            "type": "media",
            "payload": base64.b64encode(frame).decode("ascii"),
        }))

        await receive_until_media_ack(websocket)

        target_elapsed = index * FRAME_SECONDS
        actual_elapsed = time.perf_counter() - started

        if target_elapsed > actual_elapsed:
            await asyncio.sleep(target_elapsed - actual_elapsed)

        if index % 50 == 0 or index == len(frames):
            print(
                f"\rProgress: {index}/{len(frames)} frames "
                f"({index / len(frames) * 100:.0f}%)",
                end="",
                flush=True,
            )

    print("\nRecording streamed. The WebSocket remains open.")


async def main():
    if not WAV_PATH.exists():
        print(f"Recording not found: {WAV_PATH}")
        return

    call_id = input("Paste a NEW call ID: ").strip()
    if not call_id:
        print("A call ID is required.")
        return

    url = f"{SERVER_URL}/ws/call/{call_id}"

    try:
        async with websockets.connect(
            url,
            open_timeout=10,
            close_timeout=5,
        ) as websocket:
            first_event = json.loads(await websocket.recv())
            print_server_event(first_event)

            if first_event.get("type") != "ready":
                print("WebSocket did not become ready.")
                return

            await websocket.send(json.dumps({
                "type": "start",
                "call_id": call_id,
            }))

            start_response = json.loads(await websocket.recv())
            print_server_event(start_response)

            if start_response.get("type") == "error":
                print("Could not start the call.")
                return

            await stream_recording(websocket, WAV_PATH)

            print(
                "\n\nThe call is still open. Prepare another WAV recording "
                "for the caller's next response."
            )
            print("Examples: data/yes.wav, data/feedback.wav")
            print("Type 'hangup' to end the call.")
            print("Type 'exit' to disconnect the test client.")

            while True:
                answer = await asyncio.to_thread(
                    input,
                    "\nNext WAV path (or hangup/exit): ",
                )
                answer = answer.strip()

                if answer.lower() == "exit":
                    print("Disconnecting the test client.")
                    break

                if answer.lower() == "hangup":
                    await websocket.send(json.dumps({"type": "hangup"}))
                    print("Hangup requested.")
                    try:
                        while True:
                            raw = await asyncio.wait_for(
                                websocket.recv(), timeout=5
                            )
                            event = json.loads(raw)
                            print_server_event(event)
                            if event.get("type") == "hangup":
                                break
                    except asyncio.TimeoutError:
                        print("No hangup confirmation received.")
                    break

                if not answer:
                    print("Enter a WAV path, 'hangup', or 'exit'.")
                    continue

                next_path = Path(answer)
                if not next_path.is_absolute():
                    next_path = Path.cwd() / next_path

                if not next_path.is_file():
                    print(f"File not found: {next_path}")
                    continue

                await stream_recording(websocket, next_path)

    except (websockets.exceptions.WebSocketException, OSError) as exc:
        print(f"\n[CONNECTION ERROR] {exc}")
    except asyncio.TimeoutError:
        print("\n[TIMEOUT] The server did not respond in time.")
    except RuntimeError as exc:
        print(f"\n[CALL ENDED] {exc}")


if __name__ == "__main__":
    asyncio.run(main())
