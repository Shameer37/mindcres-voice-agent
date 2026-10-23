
import pytest

from app.api import websocket


@pytest.fixture
def mock_fallback(monkeypatch):
    """Provide controlled deterministic fallback values."""

    def apply(values):
        monkeypatch.setattr(
            websocket.conversation_service,
            "_basic_extract",
            lambda _text: dict(values),
        )

    return apply


def test_llm_null_removes_false_positive_fallback(mock_fallback):
    mock_fallback({
        "technician_rating": 5,
        "nps": 10,
        "other_feedback": "five stars",
    })

    result = websocket.merge_extractions(
        "I give the technician five stars.",
        {
            "technician_rating": 5,
            "nps": None,
            "other_feedback": None,
        },
    )

    assert result["technician_rating"] == 5
    assert "nps" not in result
    assert "other_feedback" not in result


def test_llm_failure_preserves_fallback(mock_fallback):
    mock_fallback({"issue_resolved": "yes"})

    result = websocket.merge_extractions(
        "The issue is fixed.",
        None,
    )

    assert result["issue_resolved"] == "yes"


def test_invalid_llm_rating_is_rejected(mock_fallback):
    mock_fallback({})

    result = websocket.merge_extractions(
        "The technician was good.",
        {
            "technician_rating": 7,
            "nps": None,
            "issue_resolved": None,
            "still_facing": None,
            "technician_on_time": None,
            "other_feedback": None,
        },
    )

    assert result is None


def test_name_alone_does_not_confirm_identity(mock_fallback):
    mock_fallback({})

    result = websocket.merge_extractions(
        "My name is Rahul.",
        {
            "identity_confirmed": False,
            "issue_resolved": None,
            "still_facing": None,
            "technician_on_time": None,
            "technician_rating": None,
            "nps": None,
            "other_feedback": None,
        },
    )

    assert result is None
