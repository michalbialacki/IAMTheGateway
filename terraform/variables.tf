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
  description = "Bedrock foundation model ID used for generation"
  type        = string
  default     = "amazon.titan-text-express-v1"
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
