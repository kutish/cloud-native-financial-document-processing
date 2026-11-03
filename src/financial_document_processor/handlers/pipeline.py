"""Step Functions task handler for OCR, extraction, validation, and failure state."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from financial_document_processor.extraction import extract_document
from financial_document_processor.models import validate_extraction
from financial_document_processor.observability import emit_metrics, log_event

_pipeline: Pipeline | None = None


class Pipeline:
    """Idempotent task implementations invoked by a Standard workflow."""

    def __init__(
        self,
        *,
        table: Any,
        s3_client: Any,
        textract_client: Any,
        bedrock_client: Any,
        artifacts_bucket: str,
        model_id: str,
        retention_days: int,
        max_ocr_characters: int,
    ) -> None:
        self._table = table
        self._s3 = s3_client
        self._textract = textract_client
        self._bedrock = bedrock_client
        self._artifacts_bucket = artifacts_bucket
        self._model_id = model_id
        self._retention_seconds = retention_days * 24 * 60 * 60
        self._max_ocr_characters = max_ocr_characters

    def begin(self, event: dict[str, Any]) -> dict[str, Any]:
        """Create the durable job and idempotently start Textract."""
        job_id = _required(event, "job_id")
        existing = self._table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        if existing and existing.get("status") == "COMPLETED":
            log_event("document_already_completed", job_id=job_id)
            return {**event, "already_completed": True}

        now = _now()
        if existing is None:
            item = {
                "job_id": job_id,
                "status": "OCR_STARTING",
                "source_bucket": _required(event, "bucket"),
                "source_key": _required(event, "key"),
                "size_bytes": int(_required(event, "size_bytes")),
                "created_at": now,
                "updated_at": now,
                "expires_at": int(time.time()) + self._retention_seconds,
            }
            try:
                self._table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(job_id)",
                )
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise

        response = self._textract.start_document_text_detection(
            DocumentLocation={
                "S3Object": {
                    "Bucket": _required(event, "bucket"),
                    "Name": _required(event, "key"),
                }
            },
            ClientRequestToken=job_id,
            JobTag=job_id,
        )
        textract_job_id = response["JobId"]
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status = :status, textract_job_id = :textract, updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "OCR_RUNNING",
                ":textract": textract_job_id,
                ":now": now,
            },
        )
        log_event("ocr_started", job_id=job_id, textract_job_id=textract_job_id)
        return {**event, "already_completed": False, "textract_job_id": textract_job_id}

    def poll_ocr(self, event: dict[str, Any]) -> dict[str, Any]:
        """Poll Textract; on completion, persist all LINE blocks outside workflow state."""
        job_id = _required(event, "job_id")
        textract_job_id = _required(event, "textract_job_id")
        response = self._textract.get_document_text_detection(
            JobId=textract_job_id, MaxResults=1000
        )
        status = response["JobStatus"]
        if status == "IN_PROGRESS":
            return {**event, "ocr_status": status}
        if status not in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
            message = response.get("StatusMessage", "Textract did not complete")
            raise RuntimeError(f"Textract {status}: {message}")

        blocks = list(response.get("Blocks", []))
        next_token = response.get("NextToken")
        while next_token:
            page = self._textract.get_document_text_detection(
                JobId=textract_job_id,
                MaxResults=1000,
                NextToken=next_token,
            )
            blocks.extend(page.get("Blocks", []))
            next_token = page.get("NextToken")

        lines = [
            {
                "page": int(block.get("Page", 1)),
                "text": block["Text"],
                "confidence": round(float(block.get("Confidence", 0)), 2),
            }
            for block in blocks
            if block.get("BlockType") == "LINE" and block.get("Text")
        ]
        page_count = int(response.get("DocumentMetadata", {}).get("Pages", 0))
        ocr_key = f"artifacts/{job_id}/ocr.json"
        self._write_json(
            ocr_key,
            {
                "job_id": job_id,
                "status": status,
                "warnings": response.get("Warnings", []),
                "page_count": page_count,
                "lines": lines,
            },
        )
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status = :status, ocr_key = :ocr_key, page_count = :pages, updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "EXTRACTING",
                ":ocr_key": ocr_key,
                ":pages": page_count,
                ":now": _now(),
            },
        )
        emit_metrics({"OcrPages": page_count}, Component="pipeline")
        log_event("ocr_completed", job_id=job_id, pages=page_count, lines=len(lines))
        return {**event, "ocr_status": status, "ocr_key": ocr_key}

    def extract(self, event: dict[str, Any]) -> dict[str, Any]:
        """Run schema-constrained Bedrock extraction and persist its raw output."""
        job_id = _required(event, "job_id")
        extraction_key = f"artifacts/{job_id}/extraction.json"
        cached = self._read_json_optional(extraction_key)
        if cached is None:
            ocr = self._read_json(_required(event, "ocr_key"))
            text, truncated = self._render_ocr_text(ocr.get("lines", []))
            raw_document, usage = extract_document(
                text,
                bedrock_client=self._bedrock,
                model_id=self._model_id,
            )
            cached = {
                "job_id": job_id,
                "model_id": self._model_id,
                "usage": usage,
                "ocr_truncated": truncated,
                "document": raw_document,
            }
            self._write_json(extraction_key, cached)
        usage = cached.get("usage", {})
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status = :status, extraction_key = :key, model_id = :model, "
                "input_tokens = :input, output_tokens = :output, updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "VALIDATING",
                ":key": extraction_key,
                ":model": cached["model_id"],
                ":input": input_tokens,
                ":output": output_tokens,
                ":now": _now(),
            },
        )
        emit_metrics(
            {"InputTokens": input_tokens, "OutputTokens": output_tokens}, Component="pipeline"
        )
        log_event("extraction_completed", job_id=job_id, model_id=cached["model_id"])
        return {**event, "extraction_key": extraction_key}

    def validate(self, event: dict[str, Any]) -> dict[str, Any]:
        """Apply deterministic business validation and publish the canonical result."""
        job_id = _required(event, "job_id")
        extraction = self._read_json(_required(event, "extraction_key"))
        document, warnings = validate_extraction(extraction["document"])
        if extraction.get("ocr_truncated"):
            warnings.append("OCR text exceeded the model input limit and was truncated")

        result_key = f"results/{job_id}.json"
        result = {
            "job_id": job_id,
            "source": {
                "bucket": _required(event, "bucket"),
                "key": _required(event, "key"),
            },
            "document": document,
            "warnings": warnings,
            "model_id": extraction["model_id"],
            "usage": extraction.get("usage", {}),
            "completed_at": _now(),
        }
        self._write_json(result_key, result)

        item = self._table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item", {})
        processing_ms = _elapsed_ms(item.get("created_at"))
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status = :status, result_key = :key, warning_count = :warnings, "
                "processing_ms = :duration, completed_at = :now, updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "COMPLETED",
                ":key": result_key,
                ":warnings": len(warnings),
                ":duration": processing_ms,
                ":now": result["completed_at"],
            },
        )
        emit_metrics(
            {"DocumentsCompleted": 1, "ProcessingDurationMs": processing_ms},
            Component="pipeline",
        )
        log_event("document_completed", job_id=job_id, result_key=result_key)
        return {"job_id": job_id, "status": "COMPLETED", "result_key": result_key}

    def fail(self, event: dict[str, Any]) -> dict[str, Any]:
        """Record a terminal workflow error without putting source text in logs."""
        job_id = str(event.get("job_id", "unknown"))
        failure = event.get("failure", {})
        error_type = str(failure.get("Error", "DocumentProcessingError"))[:200]
        cause = str(failure.get("Cause", "unknown failure"))[:1000]
        now = _now()
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #status = :status, error_type = :error, error_cause = :cause, "
                "failed_at = :now, updated_at = :now"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":error": error_type,
                ":cause": cause,
                ":now": now,
            },
        )
        emit_metrics({"DocumentFailures": 1}, Component="pipeline")
        log_event("document_failed", job_id=job_id, error_type=error_type)
        return {"job_id": job_id, "status": "FAILED", "error_type": error_type}

    def _render_ocr_text(self, lines: list[dict[str, Any]]) -> tuple[str, bool]:
        parts: list[str] = []
        previous_page: int | None = None
        length = 0
        truncated = False
        for line in lines:
            page = int(line.get("page", 1))
            prefix = f"\n--- Page {page} ---\n" if page != previous_page else "\n"
            addition = prefix + str(line.get("text", ""))
            if length + len(addition) > self._max_ocr_characters:
                remaining = self._max_ocr_characters - length
                if remaining > 0:
                    parts.append(addition[:remaining])
                truncated = True
                break
            parts.append(addition)
            length += len(addition)
            previous_page = page
        return "".join(parts).strip(), truncated

    def _write_json(self, key: str, value: dict[str, Any]) -> None:
        self._s3.put_object(
            Bucket=self._artifacts_bucket,
            Key=key,
            Body=json.dumps(value, separators=(",", ":"), sort_keys=True).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def _read_json(self, key: str) -> dict[str, Any]:
        response = self._s3.get_object(Bucket=self._artifacts_bucket, Key=key)
        value = json.loads(response["Body"].read())
        if not isinstance(value, dict):
            raise ValueError(f"artifact {key} is not a JSON object")
        return value

    def _read_json_optional(self, key: str) -> dict[str, Any] | None:
        try:
            return self._read_json(key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    pipeline = _get_pipeline()
    action = event.get("action")
    payload = event.get("job")
    if not isinstance(payload, dict):
        raise ValueError("event.job must be an object")
    actions = {
        "begin": pipeline.begin,
        "poll_ocr": pipeline.poll_ocr,
        "extract": pipeline.extract,
        "validate": pipeline.validate,
        "fail": pipeline.fail,
    }
    if action not in actions:
        raise ValueError(f"unsupported pipeline action: {action}")
    return actions[action](payload)


def _get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        table_name = os.environ["JOBS_TABLE"]
        _pipeline = Pipeline(
            table=boto3.resource("dynamodb").Table(table_name),
            s3_client=boto3.client("s3"),
            textract_client=boto3.client("textract"),
            bedrock_client=boto3.client("bedrock-runtime"),
            artifacts_bucket=os.environ["ARTIFACTS_BUCKET"],
            model_id=os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0"),
            retention_days=int(os.environ.get("JOB_RETENTION_DAYS", "30")),
            max_ocr_characters=int(os.environ.get("MAX_OCR_CHARACTERS", "120000")),
        )
    return _pipeline


def _required(value: dict[str, Any], key: str) -> Any:
    result = value.get(key)
    if result is None or result == "":
        raise ValueError(f"missing required field: {key}")
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _elapsed_ms(started_at: Any) -> int:
    if not isinstance(started_at, str):
        return 0
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    return max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
