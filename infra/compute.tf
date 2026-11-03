data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/lambda.zip"
}

resource "aws_lambda_function" "pipeline" {
  function_name = "${local.resource_prefix}-pipeline"
  description   = "Runs OCR, LLM extraction, validation, and job-state transitions"
  role          = aws_iam_role.pipeline.arn
  handler       = "financial_document_processor.handlers.pipeline.handler"
  runtime       = "python3.14"
  architectures = ["arm64"]
  memory_size   = 512
  timeout       = 60

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      ARTIFACTS_BUCKET   = aws_s3_bucket.documents.id
      BEDROCK_MODEL_ID   = var.bedrock_model_id
      JOBS_TABLE         = aws_dynamodb_table.jobs.name
      JOB_RETENTION_DAYS = tostring(var.job_retention_days)
      MAX_OCR_CHARACTERS = tostring(var.max_ocr_characters)
    }
  }

  depends_on = [aws_iam_role_policy.pipeline]
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.resource_prefix}-pipeline"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/state_machine.asl.json.tftpl", {
    pipeline_lambda_arn = aws_lambda_function.pipeline.arn
  })

  logging_configuration {
    include_execution_data = false
    level                  = "ERROR"
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
  }

  depends_on = [aws_iam_role_policy.step_functions]
}

resource "aws_lambda_function" "dispatcher" {
  function_name = "${local.resource_prefix}-dispatcher"
  description   = "Hashes uploaded documents and starts idempotent workflows"
  role          = aws_iam_role.dispatcher.arn
  handler       = "financial_document_processor.handlers.dispatch.handler"
  runtime       = "python3.14"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 30

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      MAX_DOCUMENT_BYTES = tostring(var.max_document_bytes)
      STATE_MACHINE_ARN  = aws_sfn_state_machine.pipeline.arn
    }
  }

  depends_on = [aws_iam_role_policy.dispatcher]
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.dispatcher.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]
  enabled                            = true
}

resource "aws_lambda_function" "status" {
  function_name = "${local.resource_prefix}-status"
  description   = "Returns durable job status and completed extraction results"
  role          = aws_iam_role.status.arn
  handler       = "financial_document_processor.handlers.status.handler"
  runtime       = "python3.14"
  architectures = ["arm64"]
  memory_size   = 256
  timeout       = 10

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      ARTIFACTS_BUCKET = aws_s3_bucket.documents.id
      JOBS_TABLE       = aws_dynamodb_table.jobs.name
    }
  }

  depends_on = [aws_iam_role_policy.status]
}

resource "aws_apigatewayv2_api" "status" {
  name          = "${local.resource_prefix}-status"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "status" {
  api_id                 = aws_apigatewayv2_api.status.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.status.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "status" {
  api_id             = aws_apigatewayv2_api.status.id
  route_key          = "GET /jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_stage" "status" {
  api_id      = aws_apigatewayv2_api.status.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 20
    throttling_rate_limit    = 10
  }
}

resource "aws_lambda_permission" "status_api" {
  statement_id  = "AllowStatusApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.status.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.status.execution_arn}/*/*"
}

data "aws_iam_policy_document" "status_api_caller" {
  statement {
    sid       = "ReadFinancialDocumentJobs"
    effect    = "Allow"
    actions   = ["execute-api:Invoke"]
    resources = ["${aws_apigatewayv2_api.status.execution_arn}/*/GET/jobs/*"]
  }
}

resource "aws_iam_policy" "status_api_caller" {
  name        = "${local.resource_prefix}-status-api-reader"
  description = "Attach to principals allowed to query financial document jobs"
  policy      = data.aws_iam_policy_document.status_api_caller.json
}
