"""IAM-authenticated HTTP API for durable job status and completed results."""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any

import boto3

_reader: JobReader | None = None
_JOB_ID = re.compile(r"^[0-9a-f]{64}$")


class JobReader:
    def __init__(self, *, table: Any, s3_client: Any, artifacts_bucket: str) -> None:
        self._table = table
        self._s3 = s3_client
        self._artifacts_bucket = artifacts_bucket

    def get(self, job_id: str) -> dict[str, Any] | None:
        item = self._table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        if item is None:
            return None

        response: dict[str, Any] = {
            key: _json_safe(item[key])
            for key in (
                "job_id",
                "status",
                "created_at",
                "updated_at",
                "completed_at",
                "failed_at",
                "page_count",
                "warning_count",
                "processing_ms",
                "input_tokens",
                "output_tokens",
                "model_id",
                "error_type",
            )
            if key in item
        }
        if item.get("status") == "COMPLETED" and item.get("result_key"):
            result = self._s3.get_object(Bucket=self._artifacts_bucket, Key=item["result_key"])
            response["result"] = json.loads(result["Body"].read())
        return response


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    job_id = str(event.get("pathParameters", {}).get("job_id", "")).lower()
    if not _JOB_ID.fullmatch(job_id):
        return _response(400, {"error": "job_id must be a 64-character SHA-256 hex digest"})

    job = _get_reader().get(job_id)
    if job is None:
        return _response(404, {"error": "job not found", "job_id": job_id})
    return _response(200, job)


def _get_reader() -> JobReader:
    global _reader
    if _reader is None:
        _reader = JobReader(
            table=boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"]),
            s3_client=boto3.client("s3"),
            artifacts_bucket=os.environ["ARTIFACTS_BUCKET"],
        )
    return _reader


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, separators=(",", ":"), sort_keys=True),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_json_safe(nested) for nested in value]
    return value
