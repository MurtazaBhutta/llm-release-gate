"""Typed contracts for the extraction task under evaluation.

Design note: this module deliberately does NOT cross-validate fields (e.g. assert that
`escalation_reason` is NONE whenever `decision` is POST). A contract violation by the model
is a *finding the gate must score*, not an exception that hides it. Validation that belongs
to the model's behaviour lives in `scoring.py`; validation that belongs to the wire format
lives here. See docs/adr/0002-where-validation-lives.md.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, Field


class Decision(StrEnum):
    """What the agent proposes to do with the invoice."""

    POST = "POST"
    ESCALATE = "ESCALATE"


class EscalationReason(StrEnum):
    """Closed set of reasons. A free-text reason cannot be scored, routed, or alerted on."""

    NO_JOB_MATCH = "NO_JOB_MATCH"
    AMBIGUOUS_JOB_MATCH = "AMBIGUOUS_JOB_MATCH"
    SUPPLIER_UNKNOWN = "SUPPLIER_UNKNOWN"
    SUSPECTED_DUPLICATE = "SUSPECTED_DUPLICATE"
    ALREADY_INVOICED = "ALREADY_INVOICED"
    UNREADABLE_TOTAL = "UNREADABLE_TOTAL"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    NOT_AN_INVOICE = "NOT_AN_INVOICE"
    NONE = "NONE"


class InvoiceExtraction(BaseModel):
    """The structured output the model must produce for every inbound invoice email."""

    supplier_name: str = Field(description="Supplier as named on the invoice.")
    invoice_number: str = Field(description="Supplier's own invoice number, verbatim.")
    total_amount: str = Field(
        description="Gross total as a decimal string, no thousands separator, e.g. '1284.50'."
    )
    currency: str = Field(description="ISO 4217 code, e.g. 'GBP'.")
    job_reference: str = Field(description="Matched job reference, or '' if none was matched.")
    decision: Decision
    escalation_reason: EscalationReason = Field(
        description="NONE when decision is POST; otherwise the specific reason."
    )
    confidence: float = Field(ge=0.0, le=1.0)


def parse_money(raw: str) -> Decimal | None:
    """Parse a money string to an exact Decimal, or None if it is not readable.

    Money is carried as a string end-to-end and compared as Decimal. Binary floats cannot
    represent 1284.50 exactly, so float comparison makes an eval suite flap on rounding
    rather than on model quality.
    """
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "").replace("\u00a3", "").replace("$", "")
    if not cleaned:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ArithmeticError, ValueError):
        return None
