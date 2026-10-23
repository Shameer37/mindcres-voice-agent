import asyncio
import base64
import json
import struct

import websockets

from app.audio.ulaw import encode_pcm16_to_ulaw

SERVER_URL = "ws://127.0.0.1:8000"
SAMPLES_PER_FRAME = 160  # 20 ms at 8 kHz
SILENT_FRAME = b"\xff" * SAMPLES_PER_FRAME  # G.711 μ-law silence


def make_speech_frame():
    """Generate a synthetic 20 ms audio frame with a strong signal.

    This is useful for testing VAD, but it does not contain real speech
    and should not be expected to produce meaningful words from Whisper.
    """
    pcm_samples = struct.pack(
        "<" + "h" * SAMPLES_PER_FRAME,
        *([6000] * SAMPLES_PER_FRAME),
    )
    return encode_pcm16_to_ulaw(pcm_samples)


async def receive_event(websocket):
    """Receive and print one server event."""
    raw_message = await websocket.recv()
    response = json.loads(raw_message)
    print("Server:", response)
    return response


async def send_audio(websocket, audio_bytes, label):
    """Send audio and handle events until its media acknowledgement arrives."""
    await websocket.send(json.dumps({
        "type": "media",
        "audio": base64.b64encode(audio_bytes).decode("ascii"),
    }))

    print(f"\n{label}")

    while True:
        response = await receive_event(websocket)
        message_type = response.get("type")

        if message_type == "error":
            print("The server reported an error.")
            break

        if (
            message_type == "ack"
            and response.get("event") == "media"
        ):
            break

        # Transcript events may arrive before the media acknowledgement.
        if message_type == "transcript":
            print("Recognized caller text:", response.get("text", ""))


async def main():
    call_id = input("Paste your call ID: ").strip()

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
            # The server sends its ready event after validating the call.
            ready = await receive_event(websocket)

            if ready.get("type") != "ready":
                print("WebSocket did not become ready. Stopping test.")
                return

            await websocket.send(json.dumps({
                "type": "start",
                "call_id": call_id,
                "customer_id": "CUST002",
            }))
            await receive_event(websocket)

            # 1. Send one synthetic signal frame.
            speech_frame = make_speech_frame()
            await send_audio(websocket, speech_frame, "Synthetic signal")

            # 2. Send 30 silent frames: 30 × 20 ms = 600 ms.
            # This tests the configured end-of-turn silence threshold.
            for frame_number in range(1, 31):
                await send_audio(
                    websocket,
                    SILENT_FRAME,
                    f"Silence frame {frame_number:02d}",
                )

            # 3. End the test call cleanly.
            await websocket.send(json.dumps({"type": "hangup"}))
            await receive_event(websocket)

    except websockets.exceptions.InvalidStatus as exc:
        print(f"WebSocket connection rejected: {exc}")
        print("Check that the call ID exists and is not already closed.")

    except OSError as exc:
        print(f"Could not connect to {SERVER_URL}: {exc}")
        print("Make sure the FastAPI server is running.")

    except websockets.exceptions.ConnectionClosed as exc:
        print(f"WebSocket closed unexpectedly: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
