"""SQS-triggered dispatcher with content-based execution idempotency."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

from financial_document_processor.observability import emit_metrics, log_event

_SUPPORTED_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")
_dispatcher: Dispatcher | None = None


class Dispatcher:
    """Validate an S3 notification and start one deterministic Standard workflow."""

    def __init__(
        self,
        *,
        s3_client: Any,
        step_functions_client: Any,
        state_machine_arn: str,
        max_document_bytes: int,
    ) -> None:
        self._s3 = s3_client
        self._step_functions = step_functions_client
        self._state_machine_arn = state_machine_arn
        self._max_document_bytes = max_document_bytes

    def dispatch(self, s3_record: dict[str, Any]) -> dict[str, Any]:
        event_name = str(s3_record.get("eventName", ""))
        if not event_name.startswith("ObjectCreated:"):
            return {"outcome": "ignored", "reason": "not an object-created event"}

        s3_data = s3_record["s3"]
        bucket = s3_data["bucket"]["name"]
        object_data = s3_data["object"]
        key = unquote_plus(object_data["key"])
        size = int(object_data["size"])

        if not key.lower().endswith(_SUPPORTED_SUFFIXES):
            raise ValueError(f"unsupported document type for {key}")
        if size <= 0:
            raise ValueError("document is empty")
        if size > self._max_document_bytes:
            raise ValueError(f"document is {size} bytes; limit is {self._max_document_bytes} bytes")

        digest = self._hash_object(bucket=bucket, key=key, expected_size=size)
        job_id = digest
        execution_input = {
            "job_id": job_id,
            "bucket": bucket,
            "key": key,
            "size_bytes": size,
            "etag": str(object_data.get("eTag", "")).strip('"'),
            "event_time": s3_record.get("eventTime"),
        }
        try:
            response = self._step_functions.start_execution(
                stateMachineArn=self._state_machine_arn,
                name=f"doc-{job_id}",
                input=json.dumps(execution_input, separators=(",", ":"), sort_keys=True),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
                emit_metrics({"DuplicateDocuments": 1}, Component="dispatcher")
                log_event("duplicate_document", job_id=job_id, bucket=bucket, key=key)
                return {"outcome": "duplicate", "job_id": job_id}
            raise

        emit_metrics({"DocumentsQueued": 1, "DocumentBytes": size}, Component="dispatcher")
        log_event(
            "document_queued",
            job_id=job_id,
            bucket=bucket,
            key=key,
            execution_arn=response["executionArn"],
        )
        return {
            "outcome": "queued",
            "job_id": job_id,
            "execution_arn": response["executionArn"],
        }

    def _hash_object(self, *, bucket: str, key: str, expected_size: int) -> str:
        response = self._s3.get_object(Bucket=bucket, Key=key)
        content_length = int(response.get("ContentLength", expected_size))
        if content_length != expected_size:
            raise ValueError("S3 event size does not match the current object")

        digest = hashlib.sha256()
        observed_size = 0
        for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
            if not chunk:
                continue
            observed_size += len(chunk)
            if observed_size > self._max_document_bytes:
                raise ValueError("document exceeded the configured size limit while streaming")
            digest.update(chunk)
        if observed_size != expected_size:
            raise ValueError("streamed object size does not match the S3 event")

        hexdigest = digest.hexdigest()
        declared_digest = response.get("Metadata", {}).get("sha256")
        if declared_digest and declared_digest.lower() != hexdigest:
            raise ValueError("x-amz-meta-sha256 does not match the uploaded document")
        return hexdigest


def handler(event: dict[str, Any], _context: Any) -> dict[str, list[dict[str, str]]]:
    """Return SQS partial-batch failures so successful messages are not retried."""
    dispatcher = _get_dispatcher()
    failures: list[dict[str, str]] = []
    for message in event.get("Records", []):
        message_id = message["messageId"]
        try:
            body = json.loads(message["body"])
            records = body.get("Records")
            if not isinstance(records, list) or not records:
                raise ValueError("SQS message does not contain S3 event records")
            for s3_record in records:
                dispatcher.dispatch(s3_record)
        except Exception as error:
            log_event(
                "dispatch_failed",
                message_id=message_id,
                error_type=type(error).__name__,
                error=str(error),
            )
            emit_metrics({"DispatchFailures": 1}, Component="dispatcher")
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def _get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher(
            s3_client=boto3.client("s3"),
            step_functions_client=boto3.client("stepfunctions"),
            state_machine_arn=os.environ["STATE_MACHINE_ARN"],
            max_document_bytes=int(os.environ.get("MAX_DOCUMENT_BYTES", str(20 * 1024 * 1024))),
        )
    return _dispatcher
