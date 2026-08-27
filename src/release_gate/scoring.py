"""Assertion tiers, and the asymmetry that makes them useful.

Four tiers, most objective first:

  1. structural - did the model return output matching the contract at all?
  2. fields     - are the critical fields exactly right? (invoice number, total, currency)
  3. decision   - POST vs ESCALATE
  4. reason     - is the escalation reason from the closed set, and the right one?

The tiers are deliberately not weighted into a single number. A blended score lets a model
trade a safety failure against field accuracy and still look flat overall, which is exactly
the regression an eval gate exists to catch. Tier 3 is therefore split by *direction* of
error, because the two directions do not cost the same thing:

  unsafe_post     - truth says ESCALATE, model said POST. Money moves on bad data.
  over_escalation - truth says POST, model said ESCALATE. Costs a human a minute.

Collapsing those into one "decision accuracy" number is the mistake this module avoids.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .dataset import Case
from .extractors import Outcome
from .schema import Decision, EscalationReason, parse_money

CRITICAL_FIELDS = ("invoice_number", "total_amount", "currency")


@dataclass
class CaseScore:
    case_id: str
    structural_ok: bool
    failure: str | None = None
    critical_fields_ok: bool = False
    field_mismatches: list[str] = field(default_factory=list)
    decision_ok: bool = False
    unsafe_post: bool = False
    over_escalation: bool = False
    reason_ok: bool = False
    confidence: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score_case(case: Case, outcome: Outcome) -> CaseScore:
    if outcome.parsed is None:
        # A structural failure is scored, never raised. An unparseable response is a real
        # production failure mode and belongs in the metrics, not in a stack trace.
        return CaseScore(case.id, structural_ok=False, failure=outcome.failure)

    got = outcome.parsed
    expected = case.expected
    score = CaseScore(case.id, structural_ok=True, confidence=got.confidence)

    mismatches: list[str] = []
    for name in CRITICAL_FIELDS:
        want = expected[name]
        have = getattr(got, name)
        if name == "total_amount":
            want_money = parse_money(str(want))
            same = want_money is not None and want_money == parse_money(have)
        else:
            same = str(want).strip().casefold() == str(have).strip().casefold()
        if not same:
            mismatches.append(f"{name}: expected {want!r}, got {have!r}")
    score.field_mismatches = mismatches
    score.critical_fields_ok = not mismatches

    want_decision = Decision(expected["decision"])
    score.decision_ok = got.decision == want_decision
    score.unsafe_post = want_decision is Decision.ESCALATE and got.decision is Decision.POST
    score.over_escalation = want_decision is Decision.POST and got.decision is Decision.ESCALATE

    # The reason field is a contract, not prose: NONE exactly when posting, and when
    # escalating it must be the specific expected reason. "Something looked wrong" is not
    # actionable for the back-office user who receives the escalation.
    if got.decision is Decision.POST:
        score.reason_ok = got.escalation_reason is EscalationReason.NONE
    else:
        score.reason_ok = got.escalation_reason.value == expected["escalation_reason"]

    return score


@dataclass
class RunMetrics:
    extractor: str
    n_cases: int
    structural_failures: int
    unsafe_posts: int
    over_escalations: int
    critical_field_accuracy: float
    decision_accuracy: float
    reason_validity: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> RunMetrics:
        return cls(**{name: payload[name] for name in cls.__dataclass_fields__})


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def aggregate(extractor: str, scores: list[CaseScore]) -> RunMetrics:
    n = len(scores)
    return RunMetrics(
        extractor=extractor,
        n_cases=n,
        structural_failures=sum(1 for s in scores if not s.structural_ok),
        unsafe_posts=sum(1 for s in scores if s.unsafe_post),
        over_escalations=sum(1 for s in scores if s.over_escalation),
        # Denominator is every case, not every parseable case. A model that crashes on the
        # hard half of the suite must not score 100% on the easy half.
        critical_field_accuracy=_ratio(sum(1 for s in scores if s.critical_fields_ok), n),
        decision_accuracy=_ratio(sum(1 for s in scores if s.decision_ok), n),
        reason_validity=_ratio(sum(1 for s in scores if s.reason_ok), n),
    )
