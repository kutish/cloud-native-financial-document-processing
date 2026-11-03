"""Canonical financial-document schema and deterministic validation."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_DOCUMENT_TYPES = {"invoice", "payment_order", "bank_statement", "contract", "unknown"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_STRING = 500


class DocumentValidationError(ValueError):
    """Raised when an extraction cannot be represented by the public schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_extraction(payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate model output and return a stable, JSON-safe representation."""
    if not isinstance(payload, dict):
        raise DocumentValidationError(["extraction must be an object"])

    errors: list[str] = []
    warnings: list[str] = []

    document_type = payload.get("document_type")
    if document_type not in _DOCUMENT_TYPES:
        errors.append(f"document_type must be one of {sorted(_DOCUMENT_TYPES)}")

    document_number = _optional_text(payload, "document_number", errors)
    issue_date = _optional_date(payload, "issue_date", errors)
    due_date = _optional_date(payload, "due_date", errors)
    currency = _optional_text(payload, "currency", errors, max_length=3)
    if currency is not None and not _CURRENCY.fullmatch(currency):
        errors.append("currency must be a three-letter uppercase ISO 4217 code")

    total_amount = _optional_decimal(payload, "total_amount", errors)
    if total_amount is not None and total_amount < 0:
        errors.append("total_amount must be non-negative")

    payer = _party(payload.get("payer"), "payer", errors)
    payee = _party(payload.get("payee"), "payee", errors)

    confidence = _required_decimal(payload, "confidence", errors)
    if confidence is not None and not Decimal("0") <= confidence <= Decimal("1"):
        errors.append("confidence must be between 0 and 1")

    evidence = _evidence(payload.get("source_evidence"), errors)

    if errors:
        raise DocumentValidationError(errors)

    if document_type == "unknown":
        warnings.append("document type could not be classified")
    if total_amount is None:
        warnings.append("total amount was not found")
    if currency is None:
        warnings.append("currency was not found")
    if confidence is not None and confidence < Decimal("0.7"):
        warnings.append("model confidence is below 0.7")
    if issue_date is not None and due_date is not None and due_date < issue_date:
        warnings.append("due date precedes issue date")

    document = {
        "schema_version": "1.0",
        "document_type": document_type,
        "document_number": document_number,
        "issue_date": issue_date.isoformat() if issue_date else None,
        "due_date": due_date.isoformat() if due_date else None,
        "currency": currency,
        # A decimal string avoids silently rounding financial values through JSON floats.
        "total_amount": _decimal_string(total_amount) if total_amount is not None else None,
        "payer": payer,
        "payee": payee,
        "confidence": float(confidence) if confidence is not None else None,
        "source_evidence": evidence,
    }
    return document, warnings


def _optional_text(
    payload: dict[str, Any], key: str, errors: list[str], *, max_length: int = _MAX_STRING
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a non-empty string or null")
        return None
    value = value.strip()
    if len(value) > max_length:
        errors.append(f"{key} must not exceed {max_length} characters")
        return None
    return value


def _optional_date(payload: dict[str, Any], key: str, errors: list[str]) -> date | None:
    value = _optional_text(payload, key, errors, max_length=10)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{key} must be an ISO 8601 date (YYYY-MM-DD)")
        return None


def _optional_decimal(payload: dict[str, Any], key: str, errors: list[str]) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    return _to_decimal(value, key, errors)


def _required_decimal(payload: dict[str, Any], key: str, errors: list[str]) -> Decimal | None:
    if key not in payload or payload[key] is None:
        errors.append(f"{key} is required")
        return None
    return _to_decimal(payload[key], key, errors)


def _to_decimal(value: Any, key: str, errors: list[str]) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        errors.append(f"{key} must be numeric")
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        errors.append(f"{key} must be numeric")
        return None
    if not result.is_finite():
        errors.append(f"{key} must be finite")
        return None
    return result


def _party(value: Any, key: str, errors: list[str]) -> dict[str, str | None] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object or null")
        return None
    name = _optional_text(value, "name", errors)
    account = _optional_text(value, "account", errors, max_length=128)
    if name is None and account is None:
        errors.append(f"{key} must include a name or account")
        return None
    return {"name": name, "account": account}


def _evidence(value: Any, errors: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        errors.append("source_evidence must be an array")
        return []
    if len(value) > 20:
        errors.append("source_evidence must contain at most 20 entries")
        return []

    result: list[dict[str, str]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            errors.append(f"source_evidence[{index}] must be an object")
            continue
        field = entry.get("field")
        text = entry.get("text")
        if not isinstance(field, str) or not field.strip():
            errors.append(f"source_evidence[{index}].field must be a non-empty string")
            continue
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_STRING:
            errors.append(f"source_evidence[{index}].text must be 1-{_MAX_STRING} characters")
            continue
        result.append({"field": field.strip(), "text": text.strip()})
    return result


def _decimal_string(value: Decimal) -> str:
    return format(value, "f")
