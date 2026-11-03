from __future__ import annotations

from typing import Any

import pytest

from financial_document_processor.extraction import TOOL_NAME, extract_document


class FakeBedrock:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.request: dict[str, Any] | None = None

    def converse(self, **request: Any) -> dict[str, Any]:
        self.request = request
        return self.response


def test_extract_document_returns_tool_payload_and_usage() -> None:
    document = {"document_type": "invoice", "confidence": 0.9}
    bedrock = FakeBedrock(
        {
            "output": {
                "message": {
                    "content": [
                        {"text": ""},
                        {"toolUse": {"name": TOOL_NAME, "input": document}},
                    ]
                }
            },
            "usage": {"inputTokens": 123, "outputTokens": 45},
        }
    )

    actual, usage = extract_document(
        "Invoice text", bedrock_client=bedrock, model_id="amazon.nova-lite-v1:0"
    )

    assert actual == document
    assert usage == {"input_tokens": 123, "output_tokens": 45}
    assert bedrock.request is not None
    assert bedrock.request["toolConfig"]["toolChoice"]["tool"]["name"] == TOOL_NAME


def test_extract_document_fails_closed_without_expected_tool_call() -> None:
    bedrock = FakeBedrock({"output": {"message": {"content": [{"text": "not JSON"}]}}})

    with pytest.raises(ValueError, match="did not call"):
        extract_document("text", bedrock_client=bedrock, model_id="model")
