"""Regression tests for bounded, Unicode-safe PDF export inputs."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import ExportConversationRequest, ExportTaxSummaryRequest
from app.pdf_export import generate_conversation_pdf, generate_tax_summary_pdf


def test_unicode_conversation_export_never_raises() -> None:
    request = ExportConversationRequest.model_validate(
        {"messages": [{"role": "user", "content": "Tax — café ŋ ɛ ɔ"}]}
    )

    pdf = generate_conversation_pdf([message.model_dump() for message in request.messages])

    assert pdf.startswith(b"%PDF")


def test_tax_summary_uses_decimal_amounts() -> None:
    request = ExportTaxSummaryRequest.model_validate(
        {
            "calculation": {
                "items": [{"label": "VAT", "amount": "1250.50"}],
                "total": "1250.50",
            }
        }
    )

    assert request.calculation.items[0].amount == Decimal("1250.50")
    pdf = generate_tax_summary_pdf(request.calculation.model_dump())
    assert pdf.startswith(b"%PDF")


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [{"role": "user", "content": "x" * 6_001}]},
        {"messages": [{"role": "user", "content": "x" * 6_000}] * 6},
        {"messages": [{"role": "unknown", "content": "valid"}]},
        {"messages": [{"role": "user", "content": "valid", "extra": "nope"}]},
    ],
)
def test_conversation_export_schema_rejects_unbounded_or_unknown_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        ExportConversationRequest.model_validate(payload)


def test_tax_summary_schema_rejects_non_numeric_amount() -> None:
    with pytest.raises(ValidationError):
        ExportTaxSummaryRequest.model_validate(
            {"calculation": {"items": [{"label": "VAT", "amount": "not-a-number"}]}}
        )
