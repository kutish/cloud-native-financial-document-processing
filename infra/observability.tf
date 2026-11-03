locals {
  alarm_actions = var.alarm_email == null ? [] : [aws_sns_topic.alarms[0].arn]
}

resource "aws_cloudwatch_log_group" "dispatcher" {
  name              = "/aws/lambda/${local.resource_prefix}-dispatcher"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/${local.resource_prefix}-pipeline"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "status" {
  name              = "/aws/lambda/${local.resource_prefix}-status"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "workflow" {
  name              = "/aws/vendedlogs/states/${local.resource_prefix}-pipeline"
  retention_in_days = 14
}

resource "aws_sns_topic" "alarms" {
  count = var.alarm_email == null ? 0 : 1
  name  = "${local.resource_prefix}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "dead_letter" {
  alarm_name          = "${local.resource_prefix}-dead-letter-not-empty"
  alarm_description   = "At least one document exhausted dispatcher retries."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  dimensions = {
    QueueName = aws_sqs_queue.dead_letter.name
  }
}

resource "aws_cloudwatch_metric_alarm" "pipeline_errors" {
  alarm_name          = "${local.resource_prefix}-pipeline-lambda-errors"
  alarm_description   = "The processing Lambda returned an error."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  dimensions = {
    FunctionName = aws_lambda_function.pipeline.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "workflow_failures" {
  alarm_name          = "${local.resource_prefix}-workflow-failures"
  alarm_description   = "A document-processing workflow reached its terminal failure state."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline.arn
  }
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${local.resource_prefix}-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Documents"
          region = data.aws_region.current.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["FinancialDocumentProcessor", "DocumentsQueued", "Component", "dispatcher"],
            [".", "DocumentsCompleted", "Component", "pipeline"],
            [".", "DocumentFailures", "Component", "pipeline"],
            [".", "DuplicateDocuments", "Component", "dispatcher"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Processing duration"
          region = data.aws_region.current.region
          stat   = "Average"
          period = 300
          metrics = [
            ["FinancialDocumentProcessor", "ProcessingDurationMs", "Component", "pipeline"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Model tokens"
          region = data.aws_region.current.region
          stat   = "Sum"
          period = 300
          metrics = [
            ["FinancialDocumentProcessor", "InputTokens", "Component", "pipeline"],
            [".", "OutputTokens", "Component", "pipeline"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Queue depth"
          region = data.aws_region.current.region
          stat   = "Maximum"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingest.name],
            [".", ".", ".", aws_sqs_queue.dead_letter.name],
          ]
        }
      },
    ]
  })
}

resource "aws_budgets_budget" "monthly" {
  count = var.alarm_email == null ? 0 : 1

  name         = "${local.resource_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alarm_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
  }
}
