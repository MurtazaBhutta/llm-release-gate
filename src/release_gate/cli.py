"""Command line entry points: run the suite, gate a run, record a baseline."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .dataset import load_cases, load_prompt_template, render_prompt
from .extractors import build_extractor
from .gate import DEFAULT_TOLERANCE, evaluate, load_baseline, render_markdown
from .scoring import RunMetrics, aggregate, score_case


def _cmd_run(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.dataset) if args.dataset else None)
    template = load_prompt_template(Path(args.prompt) if args.prompt else None)
    system_prompt = template.split("---USER---", 1)[0].strip()
    extractor = build_extractor(args.extractor, system_prompt=system_prompt)

    scores = []
    for case in cases:
        prompt = render_prompt(case, template.split("---USER---", 1)[-1])
        outcome = extractor.extract(case.id, prompt)
        score = score_case(case, outcome)
        scores.append(score)
        clean = score.structural_ok and score.decision_ok and score.critical_fields_ok
        print(f"[{'ok ' if clean else 'BAD'}] {case.id}: {case.intent}")
        for mismatch in score.field_mismatches:
            print(f"        {mismatch}")
        if score.unsafe_post:
            print("        UNSAFE POST - should have escalated")
        if score.failure:
            print(f"        structural failure: {score.failure}")

    metrics = aggregate(extractor.name, scores)
    payload = {"metrics": metrics.to_dict(), "cases": [s.to_dict() for s in scores]}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    print(json.dumps(metrics.to_dict(), indent=2))
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    current = RunMetrics.from_dict(run["metrics"])
    baseline = load_baseline(Path(args.baseline))
    verdict = evaluate(current, baseline, tolerance=args.tolerance)

    summary = render_markdown(current, baseline, verdict)
    print(summary)

    # GitHub renders this in the pull request's checks tab, so the failure is readable
    # without opening the job log.
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)

    return 0 if verdict.passed else 1


def _cmd_update_baseline(args: argparse.Namespace) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    current = RunMetrics.from_dict(run["metrics"])
    verdict = evaluate(current, load_baseline(Path(args.baseline)), tolerance=args.tolerance)

    if verdict.hard_failures and not args.force:
        print("refusing to record a baseline with hard failures:", file=sys.stderr)
        for item in verdict.hard_failures:
            print(f"  - {item}", file=sys.stderr)
        print("re-run with --force only if you know what you are doing", file=sys.stderr)
        return 1

    target = Path(args.baseline)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"metrics": current.to_dict()}, indent=2) + "\n", encoding="utf-8")
    print(f"recorded baseline at {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-gate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="evaluate the suite and write a run report")
    run.add_argument(
        "--extractor",
        default="fixture:good",
        help="fixture:good | fixture:regressed | claude[:model-id] | azure_openai[:deployment]",
    )
    run.add_argument("--dataset", default=None)
    run.add_argument("--prompt", default=None)
    run.add_argument("--out", default="results/run.json")
    run.set_defaults(func=_cmd_run)

    gate = sub.add_parser("gate", help="compare a run against the baseline; exit 1 on failure")
    gate.add_argument("--run", default="results/run.json")
    gate.add_argument("--baseline", default="baselines/main.json")
    gate.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    gate.set_defaults(func=_cmd_gate)

    base = sub.add_parser("update-baseline", help="record the current run as the baseline")
    base.add_argument("--run", default="results/run.json")
    base.add_argument("--baseline", default="baselines/main.json")
    base.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    base.add_argument("--force", action="store_true", help="record even with hard failures")
    base.set_defaults(func=_cmd_update_baseline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
