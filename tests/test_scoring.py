"""Scoring tests. These cover the judgement calls, not the plumbing."""

from __future__ import annotations

import pytest

from release_gate.dataset import Case
from release_gate.extractors import Outcome, _parse
from release_gate.schema import InvoiceExtraction, parse_money
from release_gate.scoring import aggregate, score_case


def make_case(**expected_overrides) -> Case:
    expected = {
        "supplier_name": "Northgate Electrical Supplies",
        "invoice_number": "NE-40192",
        "total_amount": "1284.50",
        "currency": "GBP",
        "job_reference": "J-88214",
        "decision": "POST",
        "escalation_reason": "NONE",
    }
    expected.update(expected_overrides)
    return Case(id="t", intent="test", context={}, email_body="", expected=expected)


def make_outcome(**overrides) -> Outcome:
    payload = {
        "supplier_name": "Northgate Electrical Supplies",
        "invoice_number": "NE-40192",
        "total_amount": "1284.50",
        "currency": "GBP",
        "job_reference": "J-88214",
        "decision": "POST",
        "escalation_reason": "NONE",
        "confidence": 0.9,
    }
    payload.update(overrides)
    return Outcome("t", InvoiceExtraction.model_validate(payload), "")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1284.50", "1284.50"),
        ("12,480.00", "12480.00"),
        (" 640.00 ", "640.00"),
        ("£305.75", "305.75"),
        ("1284.5", "1284.50"),
    ],
)
def test_money_normalises_before_comparison(raw: str, expected: str) -> None:
    assert parse_money(raw) == parse_money(expected)


@pytest.mark.parametrize("raw", ["", "   ", "n/a", "see attached", "1284.50.00"])
def test_unreadable_money_is_none_not_zero(raw: str) -> None:
    # None and Decimal("0.00") must stay distinguishable: "the total is zero" and "the total
    # could not be read" are different findings, and only one of them is safe to post.
    assert parse_money(raw) is None


def test_float_rounding_does_not_make_the_suite_flap() -> None:
    # The reason money is a string end to end: 0.1 + 0.2 != 0.3 in binary floats, so a
    # float-based comparison would fail cases the model got right.
    assert parse_money("0.30") == parse_money("0.3")
    assert 0.1 + 0.2 != 0.3  # the whole reason money is carried as a string


def test_structural_failure_is_scored_not_raised() -> None:
    outcome = _parse("t", '{"supplier_name": "Verrow Timber", "total_amount": "0.0')
    score = score_case(make_case(), outcome)
    assert score.structural_ok is False
    assert score.failure is not None and "invalid_json" in score.failure
    # Everything downstream stays False rather than crashing the run.
    assert score.critical_fields_ok is False
    assert score.decision_ok is False


def test_schema_violation_is_a_structural_failure() -> None:
    outcome = _parse("t", '{"supplier_name": "X", "decision": "MAYBE"}')
    score = score_case(make_case(), outcome)
    assert score.structural_ok is False
    assert "schema_violation" in (score.failure or "")


def test_unsafe_post_is_flagged_when_truth_says_escalate() -> None:
    case = make_case(decision="ESCALATE", escalation_reason="ALREADY_INVOICED")
    score = score_case(case, make_outcome(decision="POST", escalation_reason="NONE"))
    assert score.unsafe_post is True
    assert score.over_escalation is False
    assert score.decision_ok is False


def test_over_escalation_is_a_separate_signal_from_unsafe_post() -> None:
    score = score_case(
        make_case(decision="POST"),
        make_outcome(decision="ESCALATE", escalation_reason="SUPPLIER_UNKNOWN"),
    )
    assert score.over_escalation is True
    assert score.unsafe_post is False


def test_reason_must_be_none_when_posting() -> None:
    # A POST carrying an escalation reason is a contract violation even though the decision
    # itself is right - which is why reason_validity is tracked separately.
    score = score_case(make_case(), make_outcome(escalation_reason="NO_JOB_MATCH"))
    assert score.decision_ok is True
    assert score.reason_ok is False


def test_right_escalation_wrong_reason_is_caught() -> None:
    case = make_case(decision="ESCALATE", escalation_reason="AMBIGUOUS_JOB_MATCH")
    score = score_case(case, make_outcome(decision="ESCALATE", escalation_reason="NO_JOB_MATCH"))
    assert score.decision_ok is True
    assert score.reason_ok is False


def test_supplier_name_is_not_a_critical_field() -> None:
    # Deliberate: supplier naming varies legitimately ("Ltd", "&" vs "and"). Gating on it
    # would produce failures that say nothing about whether the posting was correct.
    score = score_case(make_case(), make_outcome(supplier_name="Northgate Elec. Supplies Ltd"))
    assert score.critical_fields_ok is True


def test_field_comparison_ignores_case_and_padding() -> None:
    score = score_case(make_case(), make_outcome(currency="gbp", invoice_number=" NE-40192 "))
    assert score.critical_fields_ok is True


def test_accuracy_denominator_is_all_cases_not_parseable_ones() -> None:
    good = score_case(make_case(), make_outcome())
    broken = score_case(make_case(), _parse("t", "{"))
    metrics = aggregate("test", [good, broken])
    # 1 of 2, not 1 of 1. A model that crashes on hard cases must not score 100% on easy ones.
    assert metrics.critical_field_accuracy == 0.5
    assert metrics.structural_failures == 1
