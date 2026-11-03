variable "aws_region" {
  description = "AWS Region for all resources. The default model is available in us-east-1."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Lowercase resource-name prefix."
  type        = string
  default     = "financial-docs"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.project_name))
    error_message = "project_name must be 3-25 lowercase letters, digits, or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,10}$", var.environment))
    error_message = "environment must be 2-11 lowercase letters, digits, or hyphens."
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model used by the Converse API."
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "max_document_bytes" {
  description = "Largest accepted uploaded object."
  type        = number
  default     = 20971520
}

variable "max_ocr_characters" {
  description = "Maximum OCR characters sent to Bedrock."
  type        = number
  default     = 120000
}

variable "job_retention_days" {
  description = "DynamoDB job retention and S3 artifact lifecycle."
  type        = number
  default     = 30
}

variable "alarm_email" {
  description = "Optional email for CloudWatch alarms and the monthly AWS budget."
  type        = string
  default     = null
  nullable    = true
}

variable "monthly_budget_usd" {
  description = "Monthly cost threshold when alarm_email is configured."
  type        = number
  default     = 10
}
