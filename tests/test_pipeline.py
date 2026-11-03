from __future__ import annotations

import io
import json
from typing import Any

from botocore.exceptions import ClientError

from financial_document_processor.extraction import TOOL_NAME
from financial_document_processor.handlers.pipeline import Pipeline


class FakeTable:
    def __init__(self) -> None:
        self.item: dict[str, Any] | None = None

    def get_item(self, **_request: Any) -> dict[str, Any]:
        return {"Item": dict(self.item)} if self.item is not None else {}

    def put_item(self, *, Item: dict[str, Any], **_request: Any) -> None:
        if self.item is not None:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.item = dict(Item)

    def update_item(self, **request: Any) -> None:
        if self.item is None:
            self.item = {"job_id": request["Key"]["job_id"]}
        names = request.get("ExpressionAttributeNames", {})
        values = request["ExpressionAttributeValues"]
        expression = request["UpdateExpression"].removeprefix("SET ")
        for assignment in expression.split(","):
            field, token = (part.strip() for part in assignment.split("=", 1))
            self.item[names.get(field, field)] = values[token]


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Key: str, Body: bytes, **_request: Any) -> None:
        self.objects[Key] = Body

    def get_object(self, *, Key: str, **_request: Any) -> dict[str, Any]:
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeTextract:
    def start_document_text_detection(self, **_request: Any) -> dict[str, str]:
        return {"JobId": "textract-job-1"}

    def get_document_text_detection(self, **request: Any) -> dict[str, Any]:
        if "NextToken" not in request:
            return {
                "JobStatus": "SUCCEEDED",
                "DocumentMetadata": {"Pages": 2},
                "Blocks": [
                    {
                        "BlockType": "LINE",
                        "Page": 1,
                        "Text": "Invoice INV-42",
                        "Confidence": 99.5,
                    }
                ],
                "NextToken": "page-2",
            }
        return {
            "JobStatus": "SUCCEEDED",
            "Blocks": [
                {
                    "BlockType": "LINE",
                    "Page": 2,
                    "Text": "Total USD 1000.10",
                    "Confidence": 98.0,
                }
            ],
        }


class FakeBedrock:
    def converse(self, **_request: Any) -> dict[str, Any]:
        return {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": TOOL_NAME,
                                "input": {
                                    "document_type": "invoice",
                                    "document_number": "INV-42",
                                    "issue_date": "2026-11-01",
                                    "due_date": None,
                                    "currency": "USD",
                                    "total_amount": 1000.10,
                                    "payer": None,
                                    "payee": {"name": "Example Seller", "account": None},
                                    "confidence": 0.95,
                                    "source_evidence": [
                                        {"field": "total_amount", "text": "Total USD 1000.10"}
                                    ],
                                },
                            }
                        }
                    ]
                }
            },
            "usage": {"inputTokens": 80, "outputTokens": 30},
        }


def test_pipeline_paginates_ocr_and_publishes_canonical_result() -> None:
    table = FakeTable()
    s3 = FakeS3()
    pipeline = Pipeline(
        table=table,
        s3_client=s3,
        textract_client=FakeTextract(),
        bedrock_client=FakeBedrock(),
        artifacts_bucket="documents",
        model_id="amazon.nova-lite-v1:0",
        retention_days=30,
        max_ocr_characters=120000,
    )
    job = {
        "job_id": "a" * 64,
        "bucket": "documents",
        "key": "incoming/invoice.pdf",
        "size_bytes": 123,
    }

    begun = pipeline.begin(job)
    ocr_completed = pipeline.poll_ocr(begun)
    extracted = pipeline.extract(ocr_completed)
    completed = pipeline.validate(extracted)

    assert completed == {
        "job_id": "a" * 64,
        "status": "COMPLETED",
        "result_key": f"results/{'a' * 64}.json",
    }
    ocr = json.loads(s3.objects[f"artifacts/{'a' * 64}/ocr.json"])
    assert [line["page"] for line in ocr["lines"]] == [1, 2]
    result = json.loads(s3.objects[f"results/{'a' * 64}.json"])
    assert result["document"]["total_amount"] == "1000.1"
    assert table.item is not None
    assert table.item["status"] == "COMPLETED"
    assert table.item["input_tokens"] == 80
