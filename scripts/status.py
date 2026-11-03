#!/usr/bin/env python3
"""Call the IAM-authenticated status API with the active AWS credentials."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    credentials = session.get_credentials()
    if credentials is None:
        parser.error("no AWS credentials are available")

    url = f"{args.api_url.rstrip('/')}/jobs/{args.job_id.lower()}"
    request = AWSRequest(method="GET", url=url)
    SigV4Auth(credentials.get_frozen_credentials(), "execute-api", args.region).add_auth(request)
    prepared = request.prepare()
    http_request = urllib.request.Request(
        prepared.url, headers=dict(prepared.headers.items()), method="GET"
    )
    try:
        with urllib.request.urlopen(http_request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        body = json.load(error)
        print(json.dumps(body, indent=2, sort_keys=True))
        raise SystemExit(1) from error
    print(json.dumps(body, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
