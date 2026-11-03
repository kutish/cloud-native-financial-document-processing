#!/usr/bin/env python3
"""Upload one document with the content digest used as its job ID."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path

import boto3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    path: Path = args.file
    if not path.is_file():
        parser.error(f"file does not exist: {path}")

    digest = _sha256(path)
    safe_name = path.name.replace("/", "_")
    key = f"incoming/{digest}/{safe_name}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boto3.client("s3", region_name=args.region).upload_file(
        str(path),
        args.bucket,
        key,
        ExtraArgs={
            "ContentType": content_type,
            "Metadata": {"sha256": digest},
            "ServerSideEncryption": "AES256",
        },
    )
    print(json.dumps({"bucket": args.bucket, "key": key, "job_id": digest}, indent=2))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
