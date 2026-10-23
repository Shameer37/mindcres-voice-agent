import pytest
from pydantic import ValidationError

from app.schemas import FeedbackResult


def test_valid_feedback_result():
    result = FeedbackResult(
        identity_confirmed=True,
        issue_resolved="yes",
        technician_on_time=True,
        technician_rating=5,
        nps=9,
        outcome="completed",
    )

    assert result.technician_rating == 5
    assert result.nps == 9
    assert result.unclear_fields == []


def test_partial_result_allows_unconfirmed_identity():
    result = FeedbackResult(
        identity_confirmed=None,
        outcome="caller_hangup",
        end_reason="caller_hangup",
    )

    assert result.identity_confirmed is None


@pytest.mark.parametrize("rating", [0, 6, -1, 100])
def test_invalid_technician_rating(rating):
    with pytest.raises(ValidationError):
        FeedbackResult(
            identity_confirmed=True,
            technician_rating=rating,
            outcome="completed",
        )


@pytest.mark.parametrize("nps", [-1, 11, 100])
def test_invalid_nps(nps):
    with pytest.raises(ValidationError):
        FeedbackResult(
            identity_confirmed=True,
            nps=nps,
            outcome="completed",
        )


def test_invalid_resolution_value():
    with pytest.raises(ValidationError):
        FeedbackResult(
            identity_confirmed=True,
            issue_resolved="maybe",
            outcome="completed",
        )
