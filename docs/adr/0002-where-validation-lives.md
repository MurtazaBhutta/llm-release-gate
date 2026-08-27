# ADR 0002: Model misbehaviour is scored, never raised

## Status
Accepted.

## Context
The output contract is a Pydantic model, so the obvious move is to put every rule in it -
including cross-field rules such as "`escalation_reason` must be NONE whenever `decision`
is POST".

That instinct is wrong here. If the schema rejects the response, the run raises, and the
information that the model violated its contract is converted into a traceback. A traceback
is not a metric: it cannot be counted, compared against a baseline, or trended.

There is also a subtler failure. If validation errors abort the run, the suite reports
metrics only for the cases that happened to succeed - so a model that breaks on the hard
third of the suite can post a better score than one that answers everything adequately.

## Decision
Split validation by what kind of thing is being checked.

| Kind of rule | Where it lives | Failure shows up as |
|---|---|---|
| Wire format (types, enum membership, ranges) | `schema.py` (Pydantic) | `structural_failures` |
| Model behaviour (field correctness, decision, reason consistency) | `scoring.py` | a scored tier |

`InvoiceExtraction` deliberately has no cross-field validator. A POST carrying an escalation
reason parses successfully and is then scored as a `reason_validity` failure.

Structural failures are caught and recorded as a `CaseScore`, never propagated. Accuracy
denominators are the full case count, not the parseable subset.

## Consequences
- Every failure mode appears in the metrics and can be gated on.
- A model that returns valid JSON with an incoherent decision is distinguishable from one
  that returns broken JSON. Those need different fixes.
- `schema.py` is looser than a production ingest schema would be. That is intentional: this
  schema exists to *observe* the model, not to defend a database.
