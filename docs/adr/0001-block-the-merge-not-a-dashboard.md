# ADR 0001: The gate blocks the merge; it is not a dashboard

## Status
Accepted.

## Context
Prompt and model changes are shipped like config, but they behave like code: a wording edit
can move behaviour on inputs nobody re-checked. The usual response is an evaluation
*dashboard* - scores are computed, published, and looked at when someone remembers.

A dashboard has no authority. Under delivery pressure the number is noted and the change
ships anyway, so the eval suite ends up documenting regressions rather than preventing them.

## Decision
Evaluation runs as a required status check on the pull request, and a failing gate exits
non-zero. Merging a change that degrades measured behaviour requires either fixing the
change or an explicit, visible decision to move the baseline - never silence.

Two consequences follow, and both are load-bearing:

1. **The gate must not need secrets.** A check that cannot run on a fork's pull request is
   not a required check. Hence recorded fixtures (ADR 0003).
2. **The gate must not flake.** A check that fails randomly gets bypassed, and a bypassed
   check is worse than no check because it also carries false assurance. Hence the
   tolerance band on relative metrics.

## Consequences
- A prompt change that regresses behaviour cannot merge quietly.
- Baseline movement is a reviewable diff to `baselines/main.json`, so ratcheting the
  standard down is visible in history rather than being a side effect.
- Absolute floors (unparseable output, unsafe POST) are checked independently of the
  baseline, so a bad baseline can never legitimise a safety failure.
- The cost is real: contributors wait on the check, and a genuinely improved model that
  moves a metric must have its baseline updated in the same pull request.
