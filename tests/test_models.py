from __future__ import annotations

import pytest

from financial_document_processor.models import (
    DocumentValidationError,
    validate_extraction,
)


def valid_extraction() -> dict[str, object]:
    return {
        "document_type": "invoice",
        "document_number": "INV-42",
        "issue_date": "2026-10-20",
        "due_date": "2026-11-20",
        "currency": "USD",
        "total_amount": 1000.10,
        "payer": {"name": "Example Buyer", "account": None},
        "payee": {"name": "Example Seller", "account": "GB00TEST"},
        "confidence": 0.94,
        "source_evidence": [
            {"field": "total_amount", "text": "Total USD 1,000.10"},
        ],
    }


def test_validation_returns_stable_financial_representation() -> None:
    document, warnings = validate_extraction(valid_extraction())

    assert document["schema_version"] == "1.0"
    assert document["total_amount"] == "1000.1"
    assert document["payee"] == {"name": "Example Seller", "account": "GB00TEST"}
    assert warnings == []


def test_validation_reports_plausible_but_suspicious_dates() -> None:
    payload = valid_extraction()
    payload["due_date"] = "2026-01-01"

    _, warnings = validate_extraction(payload)

    assert "due date precedes issue date" in warnings


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("currency", "usd", "currency must be"),
        ("total_amount", True, "total_amount must be numeric"),
        ("issue_date", "20/10/2026", "issue_date must be an ISO 8601 date"),
        ("confidence", 1.2, "confidence must be between 0 and 1"),
    ],
)
def test_validation_rejects_invalid_model_output(field: str, value: object, expected: str) -> None:
    payload = valid_extraction()
    payload[field] = value

    with pytest.raises(DocumentValidationError, match=expected):
        validate_extraction(payload)
