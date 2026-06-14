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
  # Must be a cross-region inference profile available in var.aws_region that
  # supports Bedrock KB RetrieveAndGenerate. Amazon Nova Lite is Amazon-native
  # (no Marketplace subscription required) and the cheapest RAG-capable option
  # in eu-central-1. Claude 3 Haiku ON_DEMAND was retired as LEGACY 2025-06.
  description = "Bedrock inference profile ID used for KB generation (RAG-supported, eu cross-region)"
  type        = string
  default     = "eu.amazon.nova-lite-v1:0"
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
