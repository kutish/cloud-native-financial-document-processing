# Changelog

## 0.1.0 - 2026-11-03

- Add content-addressed S3/SQS ingestion with partial-batch retry handling and a dead-letter queue.
- Add a Step Functions Standard workflow for asynchronous Textract OCR, Bedrock tool extraction, deterministic validation, and durable failure state.
- Add an IAM-authenticated job-status API backed by DynamoDB and private S3 results.
- Add least-privilege Terraform, CloudWatch metrics/dashboard/alarms, lifecycle retention, and optional monthly budget notifications.
- Add signed upload/status clients, behavioral tests, and GitHub Actions CI.
