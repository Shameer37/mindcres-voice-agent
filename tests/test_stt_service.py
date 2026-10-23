
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from app.services.stt_service import STTService


def make_service():
    with patch("app.services.stt_service.WhisperModel"):
        service = STTService.__new__(STTService)
        service.model = MagicMock()
        from threading import Lock
        service._lock = Lock()
        return service


def test_empty_audio_returns_empty_transcript():
    service = make_service()

    result = service.transcribe_ulaw(b"", language="en")

    assert result["text"] == ""
    assert result["language"] == "en"
    service.model.transcribe.assert_not_called()


def test_transcribes_audio_and_returns_language():
    service = make_service()

    segment = SimpleNamespace(text=" The internet is working ")
    service.model.transcribe.return_value = (
        iter([segment]),
        SimpleNamespace(language="en", language_probability=0.98),
    )

    # μ-law silence is valid audio input for this unit test.
    ulaw_audio = bytes([0xFF] * 800)

    result = service.transcribe_ulaw(ulaw_audio, language="en")

    assert result["text"] == "The internet is working"
    assert result["language"] == "en"
    assert result["language_probability"] == 0.98

    args, kwargs = service.model.transcribe.call_args
    assert isinstance(args[0], np.ndarray)
    assert args[0].dtype == np.float32
    assert kwargs["language"] == "en"


def test_model_load_is_lazy_and_local_only_by_default():
    from unittest.mock import patch

    model = MagicMock()
    model.transcribe.return_value = (
        iter([]),
        SimpleNamespace(language="en", language_probability=0.0),
    )
    with patch("app.services.stt_service.WhisperModel", return_value=model) as factory:
        service = STTService(model_size="tiny")
        assert service.model is None

        service.transcribe_ulaw(bytes([0xFF] * 2400), language="en")

    factory.assert_called_once_with(
        "tiny", device="cpu", compute_type="int8", local_files_only=True
    )


def test_selected_hindi_and_telugu_languages_reach_whisper():
    for language in ("hi", "te"):
        service = make_service()
        service.model.transcribe.return_value = (
            iter([]),
            SimpleNamespace(language=language, language_probability=0.9),
        )
        service.transcribe_ulaw(bytes([0xFF] * 800), language=language)
        assert service.model.transcribe.call_args.kwargs["language"] == language


def test_experiment_variants_change_only_selected_option():
    expected = {
        "baseline": {"beam_size": 1, "language": "en", "vad_filter": False, "condition_on_previous_text": False},
        "beam3": {"beam_size": 3, "language": "en", "vad_filter": False, "condition_on_previous_text": False},
        "auto_language": {"beam_size": 1, "language": None, "vad_filter": False, "condition_on_previous_text": False},
        "condition_context": {"beam_size": 1, "language": "en", "vad_filter": False, "condition_on_previous_text": True},
        "vad_filter": {"beam_size": 1, "language": "en", "vad_filter": True, "condition_on_previous_text": False},
    }
    for experiment, values in expected.items():
        service = make_service()
        service.experiment = experiment
        service.model.transcribe.return_value = (iter([]), SimpleNamespace(language="en", language_probability=0.9))
        service.transcribe_ulaw(bytes([0xFF] * 800), language="en")
        kwargs = service.model.transcribe.call_args.kwargs
        for key, value in values.items():
            assert kwargs[key] == value


def test_unknown_experiment_fails_clearly():
    import pytest
    with pytest.raises(ValueError, match="Unknown STT_EXPERIMENT"):
        STTService(experiment="surprise")


def test_experiment_can_be_selected_from_environment():
    from unittest.mock import patch
    with patch.dict("os.environ", {"STT_EXPERIMENT": "beam3"}):
        service = STTService()
    assert service.experiment == "beam3"
    assert service.model_size == "tiny"


def test_transcription_returns_segment_diagnostics_and_timing():
    service = make_service()
    service.model.transcribe.return_value = (
        iter([SimpleNamespace(text=" No", start=0.1, end=0.4, avg_logprob=-0.2, no_speech_prob=0.01)]),
        SimpleNamespace(language="en", language_probability=0.9),
    )
    result = service.transcribe_ulaw(bytes([0xFF] * 800), language="en")
    assert result["segments"][0]["text"] == "No"
    assert result["segments"][0]["start"] == 0.1
    assert result["stt_ms"] >= 0
