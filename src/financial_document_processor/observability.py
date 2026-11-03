"""Structured logging and CloudWatch Embedded Metric Format helpers."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

_SERVICE = "financial-document-processor"


def log_event(event: str, **fields: Any) -> None:
    """Write one machine-readable application event."""
    print(json.dumps({"service": _SERVICE, "event": event, **fields}, default=str, sort_keys=True))


def emit_metrics(metrics: Mapping[str, float | int], **dimensions: str) -> None:
    """Write CloudWatch EMF without a synchronous PutMetricData call."""
    timestamp_ms = int(time.time() * 1000)
    metric_definitions = [{"Name": name, "Unit": _metric_unit(name)} for name in sorted(metrics)]
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": timestamp_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": "FinancialDocumentProcessor",
                    "Dimensions": [sorted(dimensions)],
                    "Metrics": metric_definitions,
                }
            ],
        },
        **dimensions,
        **metrics,
    }
    print(json.dumps(payload, sort_keys=True))


def _metric_unit(name: str) -> str:
    if name.endswith("Ms"):
        return "Milliseconds"
    if name.endswith("Bytes"):
        return "Bytes"
    return "Count"
