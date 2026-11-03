locals {
  bedrock_model_arn = startswith(var.bedrock_model_id, "arn:") ? var.bedrock_model_id : "arn:${data.aws_partition.current.partition}:bedrock:${data.aws_region.current.region}::foundation-model/${var.bedrock_model_id}"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dispatcher" {
  name               = "${local.resource_prefix}-dispatcher"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "dispatcher" {
  statement {
    sid       = "WriteLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.dispatcher.arn}:*"]
  }

  statement {
    sid       = "ReadIncomingDocuments"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/incoming/*"]
  }

  statement {
    sid    = "ConsumeIngestQueue"
    effect = "Allow"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
    ]
    resources = [aws_sqs_queue.ingest.arn]
  }

  statement {
    sid       = "StartDocumentWorkflow"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
}

resource "aws_iam_role_policy" "dispatcher" {
  name   = "${local.resource_prefix}-dispatcher"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}

resource "aws_iam_role" "pipeline" {
  name               = "${local.resource_prefix}-pipeline"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "WriteLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pipeline.arn}:*"]
  }

  statement {
    sid    = "ManageJobState"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "ReadIncomingDocuments"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/incoming/*"]
  }

  statement {
    sid    = "ManageProcessingArtifacts"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [
      "${aws_s3_bucket.documents.arn}/artifacts/*",
      "${aws_s3_bucket.documents.arn}/results/*",
    ]
  }

  # Textract asynchronous job APIs do not support resource-level permissions.
  statement {
    sid    = "RunTextract"
    effect = "Allow"
    actions = [
      "textract:GetDocumentTextDetection",
      "textract:StartDocumentTextDetection",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "InvokeExtractionModel"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.bedrock_model_arn]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "${local.resource_prefix}-pipeline"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

resource "aws_iam_role" "status" {
  name               = "${local.resource_prefix}-status"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "status" {
  statement {
    sid       = "WriteLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.status.arn}:*"]
  }

  statement {
    sid       = "ReadJobState"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  statement {
    sid       = "ReadCompletedResults"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.documents.arn}/results/*"]
  }
}

resource "aws_iam_role_policy" "status" {
  name   = "${local.resource_prefix}-status"
  role   = aws_iam_role.status.id
  policy = data.aws_iam_policy_document.status.json
}

data "aws_iam_policy_document" "step_functions_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "step_functions" {
  name               = "${local.resource_prefix}-workflow"
  assume_role_policy = data.aws_iam_policy_document.step_functions_assume.json
}

data "aws_iam_policy_document" "step_functions" {
  statement {
    sid       = "InvokePipelineFunction"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.pipeline.arn, "${aws_lambda_function.pipeline.arn}:*"]
  }

  # CloudWatch Logs delivery APIs require a wildcard resource by AWS API design.
  statement {
    sid    = "DeliverWorkflowLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:DescribeLogGroups",
      "logs:DescribeResourcePolicies",
      "logs:GetLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:UpdateLogDelivery",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "step_functions" {
  name   = "${local.resource_prefix}-workflow"
  role   = aws_iam_role.step_functions.id
  policy = data.aws_iam_policy_document.step_functions.json
}
