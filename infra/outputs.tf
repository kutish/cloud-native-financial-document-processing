output "documents_bucket" {
  description = "Upload documents under incoming/ in this private bucket."
  value       = aws_s3_bucket.documents.id
}

output "jobs_table" {
  description = "DynamoDB table containing durable processing state."
  value       = aws_dynamodb_table.jobs.name
}

output "status_api_url" {
  description = "IAM-authenticated job status API base URL."
  value       = aws_apigatewayv2_api.status.api_endpoint
}

output "status_api_caller_policy_arn" {
  description = "Policy to attach to IAM principals that may query job status."
  value       = aws_iam_policy.status_api_caller.arn
}

output "dead_letter_queue_url" {
  description = "Queue containing uploads that exhausted dispatcher retries."
  value       = aws_sqs_queue.dead_letter.url
}

output "dashboard_name" {
  description = "CloudWatch operations dashboard."
  value       = aws_cloudwatch_dashboard.pipeline.dashboard_name
}
