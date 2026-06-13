variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "Project name used as prefix for all resources"
  type        = string
  default     = "iam-gateway"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod"
  }
}

variable "cognito_departments" {
  description = "Valid department names – each combined with clearance_levels to produce Cognito Groups"
  type        = list(string)
  default     = ["engineering", "legal", "finance", "hr", "security"]
}

variable "bedrock_model_id" {
  # Must be (a) supported by Bedrock KB RetrieveAndGenerate and (b) ON_DEMAND in
  # var.aws_region, so the foundation-model ARN format works without a cross-region
  # inference profile. Claude 3 Haiku is the only such model in eu-central-1.
  # NOTE: requires manual model-access enablement in the Bedrock console.
  description = "Bedrock foundation model ID used for KB generation (RAG-supported, ON_DEMAND)"
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "clearance_levels" {
  description = "Ordered clearance level names mapped to integer values"
  type        = map(number)
  default = {
    unclassified = 0
    classified   = 1
    restricted   = 2
    secret       = 3
    top_secret   = 4
  }
}
