terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  # Backend configured in backend.tf (gitignored).
  # Copy terraform/backend.tf.example → terraform/backend.tf
  # and fill in outputs from: cd terraform/bootstrap && terraform apply
}
