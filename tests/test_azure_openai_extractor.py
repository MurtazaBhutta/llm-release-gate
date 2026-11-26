"""The Azure OpenAI extractor, driven by a fake client: no network, no key, no cost.

The point of these tests is the mapping. Every way the call can fail must come back as a scored
`Outcome` failure, never an exception, using the same strings as the Claude extractor where the
failure is the same, so a run against either provider is scored and gated identically.
"""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

try:  # openai 3.x is built on httpx2; 2.x on httpx. The request/response types are the same.
    import httpx2 as httpx
except ImportError:  # pragma: no cover - depends on the installed SDK major
    import httpx

from release_gate.extractors import AzureOpenAIExtractor, build_extractor
from release_gate.schema import InvoiceExtraction

VALID = {
    "supplier_name": "Northgate Plumbing Ltd",
    "invoice_number": "NP-1042",
    "total_amount": "1250.00",
    "currency": "GBP",
    "job_reference": "J-1001",
    "decision": "POST",
    "escalation_reason": "NONE",
    "confidence": 0.93,
}


def _message(parsed=None, refusal=None, content=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(parsed=parsed, refusal=refusal, content=content)
            )
        ]
    )


class FakeClient:
    """Stands in for `openai.OpenAI`: records the request, returns or raises a scripted result."""

    def __init__(self, result):
        self.result = result
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _extractor(result, system_prompt: str = "You extract invoices."):
    client = FakeClient(result)
    return AzureOpenAIExtractor(
        "gpt-4o-invoices", system_prompt=system_prompt, client=client
    ), client


def _parsed() -> InvoiceExtraction:
    return InvoiceExtraction.model_validate(VALID)


REQUEST = httpx.Request("POST", "https://example.openai.azure.com/openai/v1/chat/completions")


def test_request_shape() -> None:
    extractor, client = _extractor(_message(parsed=_parsed()))
    extractor.extract("case-1", "the email")

    (request,) = client.requests
    assert request["model"] == "gpt-4o-invoices"  # the deployment name, not a model family
    assert request["response_format"] is InvoiceExtraction
    assert request["max_completion_tokens"] == 4096
    assert request["messages"] == [
        {"role": "system", "content": "You extract invoices."},
        {"role": "user", "content": "the email"},
    ]


def test_no_system_message_when_the_prompt_has_none() -> None:
    extractor, client = _extractor(_message(parsed=_parsed()), system_prompt="")
    extractor.extract("case-1", "the email")
    assert [m["role"] for m in client.requests[0]["messages"]] == ["user"]


def test_success_returns_the_parsed_extraction() -> None:
    extractor, _ = _extractor(_message(parsed=_parsed()))
    outcome = extractor.extract("case-1", "the email")
    assert outcome.structural_ok
    assert outcome.failure is None
    assert outcome.parsed == _parsed()
    assert InvoiceExtraction.model_validate_json(outcome.raw) == _parsed()
    assert extractor.name == "azure_openai:gpt-4o-invoices"


def _validation_error() -> Exception:
    try:
        InvoiceExtraction.model_validate({**VALID, "confidence": 1.5})
    except Exception as exc:  # noqa: BLE001 - we want the real pydantic error instance
        return exc
    raise AssertionError("expected a validation error")


@pytest.mark.parametrize(
    ("result", "failure"),
    [
        (_message(refusal="I can't help with that."), "refusal"),
        (openai.ContentFilterFinishReasonError(), "refusal"),
        (
            openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None)),
            "truncated",
        ),
        (_message(parsed=None, content="not json"), "unparseable_output"),
        (_validation_error(), "schema_violation: 1 error(s)"),
        (
            openai.RateLimitError(
                "rate limited", response=httpx.Response(429, request=REQUEST), body=None
            ),
            "api_status_429",
        ),
        (
            openai.InternalServerError(
                "server error", response=httpx.Response(500, request=REQUEST), body=None
            ),
            "api_status_500",
        ),
        (openai.APIConnectionError(request=REQUEST), "api_connection_error"),
        (openai.APITimeoutError(request=REQUEST), "api_connection_error"),
    ],
    ids=[
        "refusal",
        "content-filter",
        "truncated",
        "unparseable",
        "schema-violation",
        "rate-limit",
        "server-error",
        "connection",
        "timeout",
    ],
)
def test_failures_are_scored_not_raised(result, failure: str) -> None:
    extractor, _ = _extractor(result)
    outcome = extractor.extract("case-1", "the email")
    assert not outcome.structural_ok
    assert outcome.failure == failure


def test_build_extractor_reads_the_deployment_from_the_spec(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    extractor = build_extractor("azure_openai:gpt-4.1-prod", system_prompt="sys")
    assert isinstance(extractor, AzureOpenAIExtractor)
    assert extractor.deployment == "gpt-4.1-prod"
    assert extractor.system_prompt == "sys"
    # The v1 API: one stable path, no api-version query parameter.
    assert str(extractor._client.base_url) == "https://example.openai.azure.com/openai/v1/"


def test_build_extractor_falls_back_to_the_deployment_env_var(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-eval")
    assert build_extractor("azure_openai").name == "azure_openai:gpt-4o-eval"


def test_missing_deployment_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    with pytest.raises(ValueError, match="AZURE_OPENAI_DEPLOYMENT"):
        build_extractor("azure_openai")


def test_missing_endpoint_and_key_are_named(monkeypatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY"):
        build_extractor("azure_openai:gpt-4o")


def test_unknown_spec_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="unknown extractor spec"):
        build_extractor("openai")
