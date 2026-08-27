"""The gate: turn two sets of metrics into a merge decision.

Three verdict classes, because "did it get worse" is not one question:

  hard failure - an absolute floor was breached. Fails regardless of the baseline, so a bad
                 baseline can never legitimise a safety failure. Ratcheting a hard failure
                 into the baseline is not possible by design.
  regression   - a tracked metric fell more than `tolerance` below the baseline.
  warning      - something moved in a direction worth seeing but not worth blocking on.

`tolerance` exists because a live-model run is not perfectly reproducible; without it the
gate flakes and the team learns to bypass it, which is worse than having no gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .scoring import RunMetrics

DEFAULT_TOLERANCE = 0.02

# Metrics where higher is better and a drop past tolerance blocks the merge.
RATCHETED_METRICS = ("critical_field_accuracy", "decision_accuracy", "reason_validity")


@dataclass
class GateVerdict:
    passed: bool = True
    hard_failures: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "hard_failures": self.hard_failures,
            "regressions": self.regressions,
            "warnings": self.warnings,
            "improvements": self.improvements,
        }


def evaluate(
    current: RunMetrics,
    baseline: RunMetrics | None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> GateVerdict:
    verdict = GateVerdict()

    # --- Absolute floors. Baseline-independent on purpose. ---
    if current.structural_failures:
        verdict.hard_failures.append(
            f"{current.structural_failures} of {current.n_cases} responses did not satisfy the "
            "output contract (unparseable or schema-violating)"
        )
    if current.unsafe_posts:
        verdict.hard_failures.append(
            f"{current.unsafe_posts} unsafe POST(s): the model posted an invoice that should "
            "have been escalated. Zero tolerance - this is the failure that moves money on "
            "bad data."
        )

    if baseline is None:
        verdict.warnings.append("No baseline recorded; nothing to compare against.")
        verdict.passed = not verdict.hard_failures
        return verdict

    # --- Relative regressions, measured against the recorded baseline. ---
    for metric in RATCHETED_METRICS:
        now = getattr(current, metric)
        before = getattr(baseline, metric)
        delta = now - before
        if delta < -tolerance:
            verdict.regressions.append(
                f"{metric}: {before:.4f} -> {now:.4f} ({delta:+.4f}, tolerance {tolerance})"
            )
        elif delta > tolerance:
            verdict.improvements.append(f"{metric}: {before:.4f} -> {now:.4f} ({delta:+.4f})")

    # Over-escalation is the safe direction of failure. Worth surfacing - a model that
    # escalates everything is useless - but not worth blocking a release on.
    if current.over_escalations > baseline.over_escalations:
        verdict.warnings.append(
            f"over_escalations rose {baseline.over_escalations} -> {current.over_escalations}. "
            "Safe direction, but it pushes work back onto humans."
        )

    if current.n_cases != baseline.n_cases:
        verdict.warnings.append(
            f"suite size changed {baseline.n_cases} -> {current.n_cases}; "
            "accuracy deltas are not strictly comparable"
        )

    verdict.passed = not verdict.hard_failures and not verdict.regressions
    return verdict


def render_markdown(current: RunMetrics, baseline: RunMetrics | None, verdict: GateVerdict) -> str:
    """A summary meant to be read in a pull request, not in a log."""
    status = "PASSED" if verdict.passed else "FAILED"
    lines = [f"## LLM release gate: {status}", ""]
    lines.append(f"Extractor under test: `{current.extractor}`")
    lines.append("")
    lines.append("| Metric | Baseline | This run | Delta |")
    lines.append("|---|---|---|---|")

    def row(label: str, now: float | int, before: float | int | None, fmt: str) -> str:
        if before is None:
            return f"| {label} | - | {now:{fmt}} | - |"
        delta = now - before
        return f"| {label} | {before:{fmt}} | {now:{fmt}} | {delta:+{fmt}} |"

    def before(metric: str) -> float | int | None:
        return getattr(baseline, metric) if baseline else None

    for metric in RATCHETED_METRICS:
        lines.append(row(metric, getattr(current, metric), before(metric), ".4f"))
    for metric in ("structural_failures", "unsafe_posts", "over_escalations"):
        lines.append(row(metric, getattr(current, metric), before(metric), "d"))

    for title, items in (
        ("Hard failures", verdict.hard_failures),
        ("Regressions", verdict.regressions),
        ("Warnings", verdict.warnings),
        ("Improvements", verdict.improvements),
    ):
        if items:
            lines.extend(["", f"### {title}"])
            lines.extend(f"- {item}" for item in items)

    return "\n".join(lines) + "\n"


def load_baseline(path: Path) -> RunMetrics | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", payload)
    if not metrics:
        return None
    return RunMetrics.from_dict(metrics)
