# llm-release-gate

[![eval-gate](https://github.com/MurtazaBhutta/llm-release-gate/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/MurtazaBhutta/llm-release-gate/actions/workflows/eval-gate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**An evaluation gate that blocks the pull request when an LLM change makes behaviour worse.**

Not a dashboard, not a notebook of scores. A required status check that exits non-zero.

---

## The problem

Prompt changes ship like config and behave like code. Reword one instruction and behaviour
moves on inputs nobody thought to re-check — and unlike a code regression, nothing throws.
The output is still fluent, still well-formed, still plausible. It is just wrong more often.

The usual answer is an evaluation dashboard. Dashboards have no authority: under delivery
pressure the number gets noted and the change ships anyway, so the suite ends up documenting
regressions instead of preventing them.

This repo takes the other approach. The suite runs on every pull request, and a regression
fails the check.

## What it gates

A purchase-ledger agent reading inbound supplier invoice emails. For each one it extracts
the invoice fields and decides **POST** (record it against the matched job) or **ESCALATE**
(hand it to a human, with a specific reason). Thirteen curated cases in
[`datasets/purchase_ledger.yaml`](datasets/purchase_ledger.yaml) — clean matches, ambiguous
job matches, duplicates, already-invoiced jobs, currency mismatches, an unreadable total, a
credit note, and a marketing email that is not an invoice at all.

Every case states *what behaviour it pins*. A case that pins nothing is noise in the gate.

## 60-second demo

```bash
pip install -e ".[dev]"
make demo          # evaluate the suite, then gate it against the baseline
```

No API key required — see [ADR 0003](docs/adr/0003-deterministic-ci.md) for why CI runs on
recorded fixtures rather than the live model.

Now watch it catch a regression. `fixtures/regressed/` is the same suite as answered by a
model whose prompt was edited in a way that looked harmless:

```bash
make eval-regressed && make gate; echo "exit=$?"
```

```
## LLM release gate: FAILED

| Metric                  | Baseline | This run | Delta   |
|-------------------------|----------|----------|---------|
| critical_field_accuracy | 1.0000   | 0.8462   | -0.1538 |
| decision_accuracy       | 1.0000   | 0.7692   | -0.2308 |
| reason_validity         | 1.0000   | 0.7692   | -0.2308 |
| structural_failures     | 0        | 1        | +1      |
| unsafe_posts            | 0        | 1        | +1      |
| over_escalations        | 0        | 1        | +1      |

### Hard failures
- 1 of 13 responses did not satisfy the output contract (unparseable or schema-violating)
- 1 unsafe POST(s): the model posted an invoice that should have been escalated. Zero
  tolerance - this is the failure that moves money on bad data.

### Warnings
- over_escalations rose 0 -> 1. Safe direction, but it pushes work back onto humans.

exit=1
```

In CI that same summary renders in the pull request's checks tab, so the reviewer sees *why*
without opening a job log.

## How the gate decides

Three verdict classes, because "did it get worse" is not one question:

| Signal | Verdict | Why |
|---|---|---|
| Unparseable or schema-violating output | **hard failure**, baseline-independent | The contract either holds or it doesn't |
| `unsafe_posts > 0` — posted when it should have escalated | **hard failure**, baseline-independent | Money moves on bad data. No acceptable rate |
| Field / decision / reason accuracy drops past tolerance | **regression** vs baseline | Capability ratchet |
| `over_escalations` rises — escalated when it could have posted | **warning only** | Safe direction. Costs a human a minute, not a payment |

Hard failures are checked against absolute floors, not against the baseline. That is
deliberate: it means committing a worse baseline can never legitimise a safety failure.

### The idea worth stealing

Decision accuracy as a single number is actively misleading, because the two ways of being
wrong don't cost the same thing. Posting a bad invoice is discovered weeks later in
reconciliation and unwound by hand. Escalating a good one costs one person one minute.

Blend them and they *cancel*: a change that adds an unsafe POST while removing an
over-escalation leaves accuracy flat. That is exactly the regression a gate exists to catch.
So this repo never blends them — [ADR 0004](docs/adr/0004-asymmetric-decision-scoring.md).

## The gate tests itself

An eval gate's real failure mode isn't being wrong — it's quietly decaying into a no-op that
reports green forever. So CI runs a third job, `gate-self-test`, which feeds the gate a run
containing an unsafe POST and an unparseable response and **fails if the gate accepts it**.

`tests/test_suite_integrity.py` guards the other decay path: every case has both fixture
variants, the good variant still scores 100%, and the regressed variant still exercises all
five failure classes.

## Layout

```
prompts/extract_v1.md          the contract given to the model
datasets/purchase_ledger.yaml  13 curated cases, each with a stated intent
fixtures/good/                 recorded correct responses  -> CI baseline run
fixtures/regressed/            recorded degraded responses -> CI self-test
baselines/main.json            the metrics a change is measured against
src/release_gate/
  schema.py                    output contract (wire format only)
  extractors.py                Extractor interface: recorded fixtures | live Claude
  scoring.py                   the four assertion tiers
  gate.py                      hard failures / regressions / warnings
  cli.py                       run | gate | update-baseline
docs/adr/                      why it is built this way
```

## Against the live model

```bash
export ANTHROPIC_API_KEY=...
make eval-live      # one structured-output call per case, then gate as usual
```

Uses `client.messages.parse()` with a Pydantic `output_format`, so the response is schema-
validated at the SDK boundary. Model defaults to `claude-opus-5`, override with
`RELEASE_GATE_MODEL`. `stop_reason` is checked before content is read — a refusal returns
HTTP 200 with no usable output, and reading `.content` first would mask it as a parse error.

## Honest limits

- **All data is synthetic.** Invented suppliers, sites, job references, totals. No real
  invoice, tenant, or customer data appears anywhere in this repo.
- **Green CI does not prove the live model still behaves.** It proves the gate would catch
  it. Live drift is caught only when `make eval-live` runs. This is the deliberate trade in
  ADR 0003, not an oversight.
- **Thirteen cases is a demonstration, not coverage.** A production suite for this task
  needs hundreds, grown from real escalations.
- **The judge is deterministic.** Every assertion is exact-match or enum membership — no
  model-graded rubrics. That keeps the gate reproducible, and it means the suite can only
  check things with a correct answer. Tone and explanation quality are out of scope.
- Baseline tolerance is a flat 0.02 on every ratcheted metric. Per-metric tolerances would
  be better; the flat value is a starting point.

## Design decisions

- [ADR 0001 — The gate blocks the merge; it is not a dashboard](docs/adr/0001-block-the-merge-not-a-dashboard.md)
- [ADR 0002 — Model misbehaviour is scored, never raised](docs/adr/0002-where-validation-lives.md)
- [ADR 0003 — CI evaluates recorded fixtures, not the live model](docs/adr/0003-deterministic-ci.md)
- [ADR 0004 — The two directions of a wrong decision are scored separately](docs/adr/0004-asymmetric-decision-scoring.md)

## Provenance

Written from scratch as a public demonstration of an evaluation-gated release process. The
*pattern* is one I've shipped in production; none of the code, prompts, schemas, or data here
comes from any employer's system. Everything in this repo is original and synthetic.

MIT licensed.
