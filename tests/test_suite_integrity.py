"""Tests on the suite itself.

A regression suite is code. It drifts: a case gets added without a fixture, an expected
reason gets edited to whatever the model happened to say, a case id gets duplicated so one
silently shadows another. These tests fail on that drift instead of letting the gate keep
reporting a comfortable number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from release_gate.dataset import load_cases, load_prompt_template, render_prompt
from release_gate.extractors import FixtureExtractor
from release_gate.schema import Decision, EscalationReason, parse_money
from release_gate.scoring import aggregate, score_case

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = load_cases()


def test_suite_is_not_trivially_small() -> None:
    assert len(CASES) >= 10


def test_case_ids_are_unique() -> None:
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))


def test_suite_contains_both_decisions() -> None:
    # An all-escalate suite is passed by a model that escalates unconditionally.
    decisions = {c.expected["decision"] for c in CASES}
    assert decisions == {"POST", "ESCALATE"}
    posts = sum(1 for c in CASES if c.expected["decision"] == "POST")
    assert posts >= 3, "too few POST cases to detect an over-escalating model"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_states_its_intent(case) -> None:
    assert case.intent.strip(), f"{case.id} has no intent; a case that pins nothing is noise"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_expected_decision_and_reason_are_consistent(case) -> None:
    decision = Decision(case.expected["decision"])
    reason = EscalationReason(case.expected["escalation_reason"])
    if decision is Decision.POST:
        assert reason is EscalationReason.NONE, f"{case.id}: POST must carry reason NONE"
    else:
        assert reason is not EscalationReason.NONE, f"{case.id}: ESCALATE needs a real reason"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_expected_total_is_parseable(case) -> None:
    assert parse_money(case.expected["total_amount"]) is not None, (
        f"{case.id}: expected total is not a readable decimal string"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_has_both_fixture_variants(case) -> None:
    for variant in ("good", "regressed"):
        path = REPO_ROOT / "fixtures" / variant / f"{case.id}.json"
        assert path.exists(), f"missing {variant} fixture for {case.id}"


def test_prompt_renders_with_no_placeholders_left() -> None:
    template = load_prompt_template().split("---USER---", 1)[-1]
    for case in CASES:
        rendered = render_prompt(case, template)
        assert "{{" not in rendered, f"{case.id}: unsubstituted placeholder in prompt"
        assert case.email_body.strip().splitlines()[0] in rendered


def test_good_fixtures_agree_with_the_dataset() -> None:
    # If this fails, the recorded fixtures and the expectations have diverged, and every
    # metric the gate reports is measured against the wrong thing.
    extractor = FixtureExtractor("good")
    scores = [score_case(c, extractor.extract(c.id, "")) for c in CASES]
    result = aggregate(extractor.name, scores)
    assert result.structural_failures == 0
    assert result.unsafe_posts == 0
    assert result.critical_field_accuracy == 1.0
    assert result.decision_accuracy == 1.0
    assert result.reason_validity == 1.0


def test_regressed_fixtures_still_exercise_every_failure_class() -> None:
    # The negative fixture set is what proves the gate is not a no-op. If a refactor makes
    # these failures disappear, the CI self-test would start passing when it must not.
    extractor = FixtureExtractor("regressed")
    scores = [score_case(c, extractor.extract(c.id, "")) for c in CASES]
    result = aggregate(extractor.name, scores)
    assert result.structural_failures >= 1, "lost the unparseable-output case"
    assert result.unsafe_posts >= 1, "lost the unsafe-POST case"
    assert result.over_escalations >= 1, "lost the over-escalation case"
    assert result.critical_field_accuracy < 1.0, "lost the field-error case"
    assert result.reason_validity < 1.0, "lost the wrong-reason case"
