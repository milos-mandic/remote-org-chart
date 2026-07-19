"""Tests for classify() — the employee bucketing rules from CLAUDE.md."""

from datetime import date

from app.models import (
    BUCKET_CURRENT,
    BUCKET_FORMER,
    BUCKET_ONBOARDING,
    Person,
    classify,
)

TODAY = date(2026, 7, 19)


def person(**kw) -> Person:
    base = dict(id="x", full_name="Test Person", status="active",
               employment_lifecycle_stage="onboarded")
    base.update(kw)
    return Person(**base)


# --- CURRENT ---------------------------------------------------------------

def test_active_onboarded_is_current():
    bucket, inconsistent = classify(person(), today=TODAY)
    assert bucket == BUCKET_CURRENT
    assert inconsistent is False


def test_future_termination_is_still_current():
    # A termination date in the future means they are employed today.
    bucket, _ = classify(person(termination_date="2027-01-01"), today=TODAY)
    assert bucket == BUCKET_CURRENT


def test_termination_today_is_not_former():
    # Rule is strictly `< today`; last day still counts as employed.
    bucket, _ = classify(person(termination_date="2026-07-19"), today=TODAY)
    assert bucket == BUCKET_CURRENT


# --- FORMER ----------------------------------------------------------------

def test_archived_status_is_former():
    bucket, inconsistent = classify(person(status="archived"), today=TODAY)
    assert bucket == BUCKET_FORMER
    assert inconsistent is False  # archived is a clean former, not contradictory


def test_offboarded_lifecycle_is_former():
    bucket, _ = classify(
        person(status="archived", employment_lifecycle_stage="offboarded"),
        today=TODAY,
    )
    assert bucket == BUCKET_FORMER


def test_past_termination_is_former():
    bucket, _ = classify(person(status="archived", termination_date="2020-01-01"), today=TODAY)
    assert bucket == BUCKET_FORMER


# --- The planted inconsistency: active + former signals --------------------

def test_active_but_offboarded_is_former_and_flagged():
    bucket, inconsistent = classify(
        person(status="active", employment_lifecycle_stage="offboarded"),
        today=TODAY,
    )
    assert bucket == BUCKET_FORMER
    assert inconsistent is True


def test_active_with_past_termination_is_former_and_flagged():
    bucket, inconsistent = classify(
        person(status="active", termination_date="2024-03-01"),
        today=TODAY,
    )
    assert bucket == BUCKET_FORMER
    assert inconsistent is True


# --- ONBOARDING ------------------------------------------------------------

def test_invited_status_is_onboarding():
    for s in ("invited", "created", "initiated"):
        bucket, _ = classify(person(status=s, employment_lifecycle_stage="employment_creation"), today=TODAY)
        assert bucket == BUCKET_ONBOARDING, s


def test_self_enrollment_lifecycle_is_onboarding():
    bucket, _ = classify(
        person(status="created", employment_lifecycle_stage="employee_self_enrollment"),
        today=TODAY,
    )
    assert bucket == BUCKET_ONBOARDING


def test_former_beats_onboarding():
    # Precedence: FORMER outranks ONBOARDING when both signals are present.
    bucket, _ = classify(
        person(status="invited", employment_lifecycle_stage="offboarded"),
        today=TODAY,
    )
    assert bucket == BUCKET_FORMER


# --- Robustness ------------------------------------------------------------

def test_null_termination_does_not_crash():
    bucket, _ = classify(person(termination_date=None), today=TODAY)
    assert bucket == BUCKET_CURRENT


def test_malformed_termination_is_ignored():
    # Never crash on a bad date; just don't treat it as a termination.
    bucket, _ = classify(person(termination_date="not-a-date"), today=TODAY)
    assert bucket == BUCKET_CURRENT


def test_datetime_termination_string_parses():
    bucket, _ = classify(
        person(status="archived", termination_date="2024-03-01T00:00:00Z"),
        today=TODAY,
    )
    assert bucket == BUCKET_FORMER
