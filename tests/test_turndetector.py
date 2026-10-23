
import pytest

from app.audio.turn_detector import TurnDetector


def test_silence_before_speech_does_not_complete_turn():
    detector = TurnDetector()

    for _ in range(50):
        assert detector.process_frame(False) is False


def test_turn_completes_after_required_silence():
    detector = TurnDetector(silence_duration_ms=60)

    assert detector.process_frame(True) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is True


def test_speech_resets_silence_counter():
    detector = TurnDetector(silence_duration_ms=60)

    detector.process_frame(True)
    detector.process_frame(False)
    detector.process_frame(False)

    assert detector.process_frame(True) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is True


def test_detector_can_handle_multiple_turns():
    detector = TurnDetector(silence_duration_ms=40)

    assert detector.process_frame(True) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is True

    assert detector.process_frame(True) is False
    assert detector.process_frame(False) is False
    assert detector.process_frame(False) is True


def test_custom_frame_duration():
    detector = TurnDetector(
        silence_duration_ms=100,
        frame_duration_ms=10,
    )

    detector.process_frame(True)

    for _ in range(9):
        assert detector.process_frame(False) is False

    assert detector.process_frame(False) is True


@pytest.mark.parametrize(
    ("silence_duration_ms", "frame_duration_ms"),
    [
        (0, 20),
        (-100, 20),
        (100, 0),
        (100, -20),
    ],
)
def test_invalid_durations_are_rejected(
    silence_duration_ms,
    frame_duration_ms,
):
    with pytest.raises(ValueError):
        TurnDetector(
            silence_duration_ms=silence_duration_ms,
            frame_duration_ms=frame_duration_ms,
        )
