"""The thing under test, behind an interface.

Three implementations:

* `ClaudeExtractor`      - the real call to Claude, used to produce a run against a live model.
* `AzureOpenAIExtractor` - the same call to an Azure OpenAI deployment.
* `FixtureExtractor`     - replays recorded model outputs from disk.

Both live extractors turn every way a call can fail into a scored `Outcome` failure rather
than an exception, so the scoring and the gate treat the two providers identically.

CI runs the fixture extractor. That is a deliberate choice: a portfolio repo whose CI badge
depends on a funded API key shows a red badge to every visitor, and a gate that cannot run
without secrets cannot run on a fork's pull request either. Recorded fixtures keep the
*gate logic* under continuous test; `make eval-live` exercises the live model on demand.
See docs/adr/0003-deterministic-ci.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .schema import InvoiceExtraction

DEFAULT_MODEL = os.environ.get("RELEASE_GATE_MODEL", "claude-opus-5")


@dataclass(frozen=True)
class Outcome:
    """What came back for one case, including the ways it can fail."""

    case_id: str
    parsed: InvoiceExtraction | None
    raw: str
    failure: str | None = None

    @property
    def structural_ok(self) -> bool:
        return self.parsed is not None


class Extractor(Protocol):
    name: str

    def extract(self, case_id: str, prompt: str) -> Outcome: ...


class FixtureExtractor:
    """Replays `fixtures/<variant>/<case_id>.json`."""

    def __init__(self, variant: str, root: Path | None = None) -> None:
        self.variant = variant
        self.name = f"fixture:{variant}"
        self.root = (root or Path(__file__).resolve().parents[2] / "fixtures") / variant

    def extract(self, case_id: str, prompt: str) -> Outcome:  # noqa: ARG002 - prompt unused
        path = self.root / f"{case_id}.json"
        if not path.exists():
            return Outcome(case_id, None, "", failure=f"no fixture at {path}")
        raw = path.read_text(encoding="utf-8")
        return _parse(case_id, raw)


class ClaudeExtractor:
    """One structured-output call per case against the live model."""

    def __init__(self, model: str = DEFAULT_MODEL, system_prompt: str = "") -> None:
        import anthropic

        self.name = f"claude:{model}"
        self.model = model
        self.system_prompt = system_prompt
        self._client = anthropic.Anthropic()

    def extract(self, case_id: str, prompt: str) -> Outcome:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": prompt}],
                output_format=InvoiceExtraction,
            )
        except anthropic.APIStatusError as exc:
            return Outcome(case_id, None, "", failure=f"api_status_{exc.status_code}")
        except anthropic.APIConnectionError:
            return Outcome(case_id, None, "", failure="api_connection_error")

        # Always check stop_reason before trusting content: a refusal returns HTTP 200
        # with no usable output, and reading .content first would mask it.
        if response.stop_reason == "refusal":
            return Outcome(case_id, None, "", failure="refusal")

        parsed = response.parsed_output
        raw = parsed.model_dump_json() if parsed is not None else ""
        if parsed is None:
            return Outcome(case_id, None, raw, failure="unparseable_output")
        return Outcome(case_id, parsed, raw)


class AzureOpenAIExtractor:
    """One structured-output call per case against an Azure OpenAI deployment.

    Uses the Azure OpenAI v1 API (``<endpoint>/openai/v1/``) through the standard ``openai``
    client, so there is no ``api_version`` to pin. The model name is the *deployment* name.
    Structured outputs need a deployment that supports them: gpt-4o 2024-08-06 or later,
    gpt-4.1, or the gpt-5 family.
    """

    def __init__(
        self,
        deployment: str | None = None,
        system_prompt: str = "",
        client: object | None = None,
    ) -> None:
        deployment = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
        if not deployment:
            raise ValueError(
                "no Azure OpenAI deployment: set AZURE_OPENAI_DEPLOYMENT or use "
                "--extractor azure_openai:<deployment>"
            )
        self.name = f"azure_openai:{deployment}"
        self.deployment = deployment
        self.system_prompt = system_prompt
        self._client = client if client is not None else _azure_client()

    def extract(self, case_id: str, prompt: str) -> Outcome:
        import openai

        messages = [{"role": "user", "content": prompt}]
        if self.system_prompt:
            messages.insert(0, {"role": "system", "content": self.system_prompt})
        try:
            completion = self._client.chat.completions.parse(
                model=self.deployment,
                messages=messages,
                response_format=InvoiceExtraction,
                max_completion_tokens=4096,
            )
        except openai.ContentFilterFinishReasonError:
            # Azure's content filter is this provider's refusal: no usable output.
            return Outcome(case_id, None, "", failure="refusal")
        except openai.LengthFinishReasonError:
            return Outcome(case_id, None, "", failure="truncated")
        except ValidationError as exc:
            # Valid JSON for the schema can still fail a Pydantic rule (e.g. confidence > 1).
            return Outcome(
                case_id, None, "", failure=f"schema_violation: {exc.error_count()} error(s)"
            )
        except openai.APIStatusError as exc:
            return Outcome(case_id, None, "", failure=f"api_status_{exc.status_code}")
        except openai.APIConnectionError:
            return Outcome(case_id, None, "", failure="api_connection_error")

        message = completion.choices[0].message
        # A refusal arrives as a normal 200 response with `refusal` set and nothing parsed.
        if message.refusal:
            return Outcome(case_id, None, "", failure="refusal")

        parsed = message.parsed
        if parsed is None:
            return Outcome(case_id, None, message.content or "", failure="unparseable_output")
        return Outcome(case_id, parsed, parsed.model_dump_json())


def _azure_client() -> object:
    import openai

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    missing = [
        name
        for name, value in (("AZURE_OPENAI_ENDPOINT", endpoint), ("AZURE_OPENAI_API_KEY", api_key))
        if not value
    ]
    if missing:
        raise ValueError(f"Azure OpenAI is not configured: set {' and '.join(missing)}")
    return openai.OpenAI(base_url=f"{endpoint.rstrip('/')}/openai/v1/", api_key=api_key)


def _parse(case_id: str, raw: str) -> Outcome:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Outcome(case_id, None, raw, failure=f"invalid_json: {exc.msg}")
    try:
        return Outcome(case_id, InvoiceExtraction.model_validate(payload), raw)
    except ValidationError as exc:
        return Outcome(
            case_id, None, raw, failure=f"schema_violation: {exc.error_count()} error(s)"
        )


def build_extractor(spec: str, system_prompt: str = "") -> Extractor:
    """`fixture:<variant>`, `claude[:<model-id>]` or `azure_openai[:<deployment>]`."""
    if spec.startswith("fixture:"):
        return FixtureExtractor(spec.split(":", 1)[1])
    if spec == "claude":
        return ClaudeExtractor(system_prompt=system_prompt)
    if spec.startswith("claude:"):
        return ClaudeExtractor(model=spec.split(":", 1)[1], system_prompt=system_prompt)
    if spec == "azure_openai":
        return AzureOpenAIExtractor(system_prompt=system_prompt)
    if spec.startswith("azure_openai:"):
        return AzureOpenAIExtractor(deployment=spec.split(":", 1)[1], system_prompt=system_prompt)
    raise ValueError(f"unknown extractor spec: {spec!r}")
