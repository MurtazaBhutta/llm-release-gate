"""Gate tests. The gate is the part that must never silently become a no-op."""

from __future__ import annotations

from release_gate.gate import evaluate, render_markdown
from release_gate.scoring import RunMetrics


def metrics(**overrides) -> RunMetrics:
    base = {
        "extractor": "test",
        "n_cases": 13,
        "structural_failures": 0,
        "unsafe_posts": 0,
        "over_escalations": 0,
        "critical_field_accuracy": 1.0,
        "decision_accuracy": 1.0,
        "reason_validity": 1.0,
    }
    base.update(overrides)
    return RunMetrics(**base)


def test_clean_run_against_equal_baseline_passes() -> None:
    verdict = evaluate(metrics(), metrics())
    assert verdict.passed is True
    assert verdict.hard_failures == []
    assert verdict.regressions == []


def test_structural_failure_is_a_hard_failure() -> None:
    verdict = evaluate(metrics(structural_failures=1), metrics())
    assert verdict.passed is False
    assert any("output contract" in item for item in verdict.hard_failures)


def test_unsafe_post_fails_even_when_the_baseline_also_had_one() -> None:
    # The point of an absolute floor: a bad baseline must not be able to licence a safety
    # failure. If this ever passes, the gate has been ratcheted into uselessness.
    verdict = evaluate(metrics(unsafe_posts=1), metrics(unsafe_posts=1))
    assert verdict.passed is False
    assert any("unsafe POST" in item for item in verdict.hard_failures)


def test_unsafe_post_fails_with_no_baseline_at_all() -> None:
    verdict = evaluate(metrics(unsafe_posts=2), None)
    assert verdict.passed is False


def test_accuracy_drop_beyond_tolerance_is_a_regression() -> None:
    verdict = evaluate(metrics(decision_accuracy=0.90), metrics(), tolerance=0.02)
    assert verdict.passed is False
    assert any("decision_accuracy" in item for item in verdict.regressions)


def test_drop_inside_tolerance_is_absorbed() -> None:
    # Live-model runs are not bit-reproducible. Without tolerance the gate flakes, and a
    # flaky gate gets bypassed, which is worse than no gate.
    verdict = evaluate(metrics(decision_accuracy=0.99), metrics(), tolerance=0.02)
    assert verdict.passed is True
    assert verdict.regressions == []


def test_improvement_is_recorded_but_does_not_move_the_baseline() -> None:
    verdict = evaluate(metrics(reason_validity=1.0), metrics(reason_validity=0.80))
    assert verdict.passed is True
    assert any("reason_validity" in item for item in verdict.improvements)


def test_over_escalation_warns_but_does_not_block() -> None:
    # Escalating a valid invoice costs a human a minute. Posting a bad one costs money.
    # The gate must not treat those as the same event.
    verdict = evaluate(metrics(over_escalations=3), metrics(over_escalations=0))
    assert verdict.passed is True
    assert any("over_escalations" in item for item in verdict.warnings)


def test_missing_baseline_passes_with_a_warning() -> None:
    verdict = evaluate(metrics(), None)
    assert verdict.passed is True
    assert any("No baseline" in item for item in verdict.warnings)


def test_suite_size_change_is_flagged_as_not_comparable() -> None:
    verdict = evaluate(metrics(n_cases=20), metrics(n_cases=13))
    assert any("suite size changed" in item for item in verdict.warnings)


def test_markdown_summary_states_the_verdict_and_survives_no_baseline() -> None:
    failing = evaluate(metrics(unsafe_posts=1), metrics())
    assert "FAILED" in render_markdown(metrics(unsafe_posts=1), metrics(), failing)

    passing = evaluate(metrics(), None)
    summary = render_markdown(metrics(), None, passing)
    assert "PASSED" in summary
