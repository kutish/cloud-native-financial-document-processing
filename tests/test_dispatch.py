from __future__ import annotations

import hashlib
import io
import json
from typing import Any

from botocore.exceptions import ClientError

from financial_document_processor.handlers.dispatch import Dispatcher


class StreamingBody:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def iter_chunks(self, chunk_size: int) -> Any:
        stream = io.BytesIO(self._value)
        while chunk := stream.read(chunk_size):
            yield chunk


class FakeS3:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def get_object(self, **_request: Any) -> dict[str, Any]:
        return {
            "Body": StreamingBody(self.value),
            "ContentLength": len(self.value),
            "Metadata": {"sha256": hashlib.sha256(self.value).hexdigest()},
        }


class FakeStepFunctions:
    def __init__(self, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.request: dict[str, Any] | None = None

    def start_execution(self, **request: Any) -> dict[str, Any]:
        self.request = request
        if self.duplicate:
            raise ClientError(
                {"Error": {"Code": "ExecutionAlreadyExists", "Message": "duplicate"}},
                "StartExecution",
            )
        return {"executionArn": "arn:aws:states:us-east-1:123:execution:test:one"}


def s3_record(value: bytes) -> dict[str, Any]:
    return {
        "eventName": "ObjectCreated:Put",
        "eventTime": "2026-11-03T10:00:00Z",
        "s3": {
            "bucket": {"name": "documents"},
            "object": {"key": "incoming%2Finvoice.pdf", "size": len(value), "eTag": "etag"},
        },
    }


def test_dispatch_uses_content_hash_as_stable_execution_identity() -> None:
    value = b"synthetic financial document"
    workflows = FakeStepFunctions()
    dispatcher = Dispatcher(
        s3_client=FakeS3(value),
        step_functions_client=workflows,
        state_machine_arn="arn:aws:states:us-east-1:123:stateMachine:documents",
        max_document_bytes=1024,
    )

    result = dispatcher.dispatch(s3_record(value))

    digest = hashlib.sha256(value).hexdigest()
    assert result["job_id"] == digest
    assert workflows.request is not None
    assert workflows.request["name"] == f"doc-{digest}"
    assert json.loads(workflows.request["input"])["key"] == "incoming/invoice.pdf"


def test_dispatch_treats_existing_execution_as_successful_duplicate() -> None:
    value = b"same document"
    dispatcher = Dispatcher(
        s3_client=FakeS3(value),
        step_functions_client=FakeStepFunctions(duplicate=True),
        state_machine_arn="arn:state-machine",
        max_document_bytes=1024,
    )

    result = dispatcher.dispatch(s3_record(value))

    assert result == {"outcome": "duplicate", "job_id": hashlib.sha256(value).hexdigest()}
