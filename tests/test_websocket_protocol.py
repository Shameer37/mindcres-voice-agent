import base64
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import websocket as websocket_api


def test_assignment_event_key_and_legacy_type_are_supported():
    assert websocket_api.websocket_event_type({"event": "start"}) == "start"
    assert websocket_api.websocket_event_type({"type": "start"}) == "start"
    assert websocket_api.websocket_event_type({"event": "media", "type": "start"}) == "media"


def test_silence_watchdog_arms_only_after_greeting_playback_finishes():
    task = object()
    assert not websocket_api.no_input_watchdog_ready(True, None, None, False)
    assert not websocket_api.no_input_watchdog_ready(True, 1.0, task, False)
    assert not websocket_api.no_input_watchdog_ready(True, 1.0, None, True)
    armed_at = websocket_api.arm_question_timer(True, 10.0)
    assert armed_at == 10.0
    assert websocket_api.no_input_watchdog_ready(True, armed_at, None, False)
    assert websocket_api.arm_question_timer(False, 11.0) is None


def test_response_window_begins_at_playback_completion_not_prompt_creation():
    prompt_created_at = 20.0
    provider_and_playback_seconds = 11.0
    playback_completed_at = prompt_created_at + provider_and_playback_seconds
    armed_at = websocket_api.arm_question_timer(True, playback_completed_at)

    assert armed_at == 31.0
    # Provider generation and playback time are outside caller silence.
    assert websocket_api.no_input_action(0, 7, False) is None
    assert websocket_api.no_input_action(7.39, 7, False) is None
    assert websocket_api.no_input_action(7.40, 7, False) == "remind"


def test_immediate_and_delayed_turns_do_not_expire_before_policy_window():
    for elapsed in (0, 3, 5, 7):
        assert websocket_api.no_input_action(elapsed, 9, False) is None
    for elapsed in (0, 3, 5, 7):
        assert websocket_api.no_input_action(elapsed, 7, False) is None


def test_response_windows_and_single_reminder_policy():
    assert websocket_api.INITIAL_RESPONSE_TIMEOUT_SEC == 9
    assert websocket_api.NO_INPUT_TIMEOUT_SEC == 7
    assert websocket_api.REMINDER_GRACE_SEC == 6
    assert websocket_api.no_input_action(9.39, 9, False) is None
    assert websocket_api.no_input_action(9.40, 9, False) == "remind"
    assert websocket_api.no_input_action(6.39, 9, True) is None
    assert websocket_api.no_input_action(6.40, 9, True) == "hangup"
    assert websocket_api.no_input_action(60, 9, True) == "hangup"


def test_timeout_edge_grace_allows_caller_frame_to_win_timer_race():
    # A caller frame arriving on the next 20 ms receive cycle remains ahead
    # of timeout handling; this is the race window for the paced WS protocol.
    assert websocket_api.no_input_action(9.30, 9, False) is None
    assert websocket_api.no_input_action(9.50, 9, False) == "remind"


def test_partial_call_with_saved_answers_is_not_no_response():
    websocket_api.conversation_service.start("partial-timeout", "CUST001")
    state = websocket_api.conversation_service.get_state("partial-timeout")
    state.feedback.update(
        identity_confirmed=True,
        issue_resolved="yes",
        technician_on_time=True,
    )
    result = websocket_api.get_partial_result("partial-timeout", "no_response")
    assert result["outcome"] == "partial"
    assert result["needs_followup"] is False
    assert result["identity_confirmed"] is True
    websocket_api.conversation_service.forget("partial-timeout")


def test_no_input_with_no_saved_answers_remains_no_response():
    websocket_api.conversation_service.start("silent-timeout", "CUST001")
    result = websocket_api.get_partial_result("silent-timeout", "no_response")
    assert result["outcome"] == "no_response"
    assert result["needs_followup"] is False
    websocket_api.conversation_service.forget("silent-timeout")


@pytest.mark.asyncio
async def test_tts_sends_base64_160_byte_frames_and_playback_mark(monkeypatch):
    frames = [bytes([0x7F]) * 160, bytes([0xFF]) * 160]
    monkeypatch.setattr(
        websocket_api.tts_service,
        "synthesize_frames",
        AsyncMock(return_value=frames),
    )
    ws = AsyncMock()

    await websocket_api.send_tts_audio(ws, "hello")

    media_messages = [
        call.args[0]
        for call in ws.send_json.await_args_list
        if (call.args[0].get("event") or call.args[0].get("type")) == "media"
    ]
    assert [
        base64.b64decode(message["media"]["payload"])
        for message in media_messages
    ] == frames
    assert all(len(base64.b64decode(m["media"]["payload"])) == 160 for m in media_messages)
    assert (ws.send_json.await_args_list[-1].args[0]["event"]) == "mark"


@pytest.mark.asyncio
async def test_tts_rejects_malformed_frame(monkeypatch):
    monkeypatch.setattr(
        websocket_api.tts_service,
        "synthesize_frames",
        AsyncMock(return_value=[b"short"]),
    )
    with pytest.raises(ValueError, match="exactly 160 bytes"):
        await websocket_api.send_tts_audio(AsyncMock(), "hello")


@pytest.mark.asyncio
async def test_tts_waits_for_client_playback_mark_ack(monkeypatch):
    monkeypatch.setattr(
        websocket_api.tts_service,
        "synthesize_frames",
        AsyncMock(return_value=[bytes([0x7F]) * 160]),
    )
    ack = asyncio.Event()
    ws = AsyncMock()
    task = asyncio.create_task(
        websocket_api.send_tts_audio(ws, "hello", mark_ack_event=ack)
    )
    await asyncio.sleep(0)
    assert not task.done()
    ack.set()
    await asyncio.wait_for(task, timeout=1)


def test_playback_mark_ack_is_correlated_and_idempotent():
    event = asyncio.Event()
    ws = SimpleNamespace(_skyfiber_mark_ack_events={"expected": event})

    assert not websocket_api.acknowledge_playback_mark(ws, {"name": "stale"})
    assert not event.is_set()
    assert websocket_api.acknowledge_playback_mark(ws, {"name": "expected"})
    assert event.is_set()
    assert not websocket_api.acknowledge_playback_mark(ws, {"name": "expected"})


@pytest.mark.asyncio
async def test_mark_ack_stops_barge_in_detection_before_task_scheduled_done():
    event = asyncio.Event()

    async def wait_for_ack():
        await event.wait()

    task = asyncio.create_task(wait_for_ack())
    task._skyfiber_mark_ack_name = "expected"
    ws = SimpleNamespace(_skyfiber_mark_ack_events={"expected": event})
    assert websocket_api.playback_audio_is_active(ws, task)
    assert websocket_api.acknowledge_playback_mark(ws, {"name": "expected"})
    assert not websocket_api.playback_audio_is_active(ws, task)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_playback_drains_mark_before_hangup():
    event = asyncio.Event()
    received = asyncio.Event()

    async def receive_json():
        received.set()
        return {"event": "mark", "name": "terminal-mark"}

    ws = SimpleNamespace(
        _skyfiber_mark_ack_events={"terminal-mark": event},
        receive_json=receive_json,
    )
    playback = asyncio.create_task(event.wait())
    drain = asyncio.create_task(
        websocket_api.finish_playback_while_receiving(ws, playback, "call-1")
    )
    assert await asyncio.wait_for(received.wait(), timeout=1)
    assert await asyncio.wait_for(drain, timeout=1)
    assert playback.done()


@pytest.mark.asyncio
async def test_terminal_event_is_sent_before_close():
    calls = []

    async def send_json(message):
        calls.append(("send", message))

    async def close():
        calls.append(("close", None))

    ws = SimpleNamespace(send_json=send_json, close=close)
    assert await websocket_api.send_terminal_event(ws, "call-1", "wrong_person")
    assert calls[0][0] == "send"
    assert calls[0][1]["event"] == "hangup"
    assert calls[1] == ("close", None)


@pytest.mark.asyncio
async def test_terminal_send_failure_still_attempts_socket_close():
    calls = []

    async def send_json(_message):
        calls.append("send")
        raise OSError("disconnected")

    async def close():
        calls.append("close")

    ws = SimpleNamespace(send_json=send_json, close=close)
    assert not await websocket_api.send_terminal_event(ws, "call-2", "caller_hangup")
    assert calls == ["send", "close"]


@pytest.mark.asyncio
async def test_tts_disconnect_during_playback_is_consumed(monkeypatch):
    async def disconnected(**_kwargs):
        raise WebSocketDisconnect(code=1000)

    monkeypatch.setattr(websocket_api, "send_tts_audio", disconnected)
    ws = SimpleNamespace(send_json=AsyncMock())
    task = await websocket_api.save_and_send_bot_reply(
        websocket=ws,
        db=SimpleNamespace(add=Mock(), commit=Mock(), rollback=Mock()),
        call_id="disconnect-during-tts",
        text="Goodbye.",
    )
    await asyncio.wait_for(task, timeout=1)
    assert task.done()
    assert task.exception() is None
