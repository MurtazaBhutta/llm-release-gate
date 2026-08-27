# ADR 0004: The two directions of a wrong decision are scored separately

## Status
Accepted.

## Context
The agent decides POST or ESCALATE, so the obvious metric is decision accuracy: what
fraction of cases got the right label. It is one number, it trends nicely, and it is
actively misleading.

The two ways of being wrong do not cost the same thing:

- **Unsafe POST** - truth says escalate, the model posts. A payment is recorded against the
  wrong job, or twice, or at the wrong value. Discovered weeks later during reconciliation,
  unwound by hand, and it damages trust in the whole system.
- **Over-escalation** - truth says post, the model escalates. A human spends a minute on
  something that did not need them.

A single accuracy figure treats those as one event. Worse, it lets them cancel: a change
that adds an unsafe POST while removing an over-escalation leaves accuracy flat. That is
precisely the regression a gate exists to catch, and a blended score hides it.

## Decision
Do not blend. Track three separate quantities and gate them differently:

| Signal | Gate behaviour | Rationale |
|---|---|---|
| `unsafe_posts` | hard failure at > 0, baseline-independent | Money moves on bad data. There is no acceptable rate. |
| `decision_accuracy` | regression if it falls past tolerance | Overall capability, ratcheted against baseline. |
| `over_escalations` | warning only | Safe direction. Worth watching - a model that escalates everything is useless - but not worth blocking a release. |

The same principle applies at the tier level: structural, field, decision, and reason
accuracy are reported separately rather than as a weighted composite, so a change cannot
trade one against another and still look flat.

## Consequences
- The gate cannot be satisfied by a model that gets safer on average while introducing one
  catastrophic case.
- Because `unsafe_posts` is checked against zero rather than against the baseline, it cannot
  be ratcheted away by committing a worse baseline.
- The suite must contain enough POST cases to detect an over-escalating model; an
  all-escalate suite would be passed by a model that escalates unconditionally. A test in
  `test_suite_integrity.py` enforces this.
- More metrics means a busier report. Accepted: the report is read at merge time by someone
  deciding whether to ship, and that reader needs the breakdown, not a single figure.
