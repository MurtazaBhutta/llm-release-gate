# ADR 0003: CI evaluates recorded fixtures, not the live model

## Status
Accepted.

## Context
The natural design calls the model in CI. Three problems, in increasing order of severity:

1. **Cost and time.** Every push pays for a full suite run.
2. **Flake.** Sampling variance moves metrics between identical runs, so the gate blocks
   merges for reasons unrelated to the change.
3. **Secrets.** A workflow needing `ANTHROPIC_API_KEY` cannot run on a pull request from a
   fork, so it cannot be a required check for outside contributions - and a public repo
   whose CI badge depends on a funded key eventually shows red for billing reasons.

Point 3 conflicts directly with ADR 0001, which requires the gate to be a required check.

## Decision
Two extraction paths behind one `Extractor` interface:

- `fixture:<variant>` replays recorded model outputs from `fixtures/`. Deterministic, free,
  no credentials. This is what CI runs.
- `claude[:model-id]` calls the live model. Run locally or on demand via `make eval-live`.

CI therefore holds the *gate logic* - scoring tiers, thresholds, baseline comparison, the
markdown summary - under continuous test on every push. Live-model behaviour is measured
when someone chooses to spend money on it, and the result is committed as a new baseline.

A third job, `gate-self-test`, feeds the gate a deliberately degraded fixture set and fails
if the gate accepts it.

## Consequences
- CI is fast, free, runs on forks, and its badge means something.
- **The honest limit: green CI here does not prove the live model still behaves.** It proves
  the gate would catch it. Live drift is caught only when `make eval-live` is run.
- Fixtures can drift from the dataset, so `tests/test_suite_integrity.py` asserts that every
  case has both variants and that the good variant scores 100%.
- The recorded-fixture path is also the reason the gate is testable at all: the negative
  self-test needs a reproducible bad run, which a live model cannot reliably provide.
