"""Loading the regression suite and rendering each case into a prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "datasets" / "purchase_ledger.yaml"
DEFAULT_PROMPT = REPO_ROOT / "prompts" / "extract_v1.md"


@dataclass(frozen=True)
class Case:
    id: str
    intent: str
    context: dict[str, Any]
    email_body: str
    expected: dict[str, Any]

    @property
    def expects_escalation(self) -> bool:
        return self.expected["decision"] == "ESCALATE"


def load_cases(path: Path | None = None) -> list[Case]:
    raw = yaml.safe_load((path or DEFAULT_DATASET).read_text(encoding="utf-8"))
    cases = [
        Case(
            id=entry["id"],
            intent=entry["intent"],
            context=entry.get("context", {}),
            email_body=entry["email_body"],
            expected=entry["expected"],
        )
        for entry in raw["cases"]
    ]
    ids = [c.id for c in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids in dataset: {sorted(duplicates)}")
    return cases


def render_context(context: dict[str, Any]) -> str:
    """Flatten the ledger context into the smallest form that still supports the decision.

    Only agent-relevant fields are rendered. A real ledger record carries dozens of columns;
    sending all of them costs tokens on every call and gives the model room to key off data
    that has nothing to do with the decision.
    """
    lines: list[str] = []
    suppliers = context.get("known_suppliers") or []
    lines.append("Known suppliers: " + (", ".join(suppliers) if suppliers else "(none)"))

    jobs = context.get("open_jobs") or []
    if not jobs:
        lines.append("Open jobs: (none)")
    else:
        lines.append("Open jobs:")
        for job in jobs:
            flags = []
            if job.get("already_invoiced"):
                flags.append("ALREADY_INVOICED")
            if job.get("closed"):
                flags.append("CLOSED")
            suffix = " [" + ", ".join(flags) + "]" if flags else ""
            lines.append(
                "  - {ref} | site: {site} | supplier: {supplier}{suffix}".format(
                    ref=job["ref"], site=job["site"], supplier=job["supplier"], suffix=suffix
                )
            )

    seen = context.get("invoices_already_seen") or []
    lines.append(
        "Invoice numbers already on the ledger: " + (", ".join(seen) if seen else "(none)")
    )
    return "\n".join(lines)


def load_prompt_template(path: Path | None = None) -> str:
    return (path or DEFAULT_PROMPT).read_text(encoding="utf-8")


def render_prompt(case: Case, template: str) -> str:
    return template.replace("{{CONTEXT}}", render_context(case.context)).replace(
        "{{EMAIL_BODY}}", case.email_body.strip()
    )
