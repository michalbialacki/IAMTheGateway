locals {
  lambda_role_name  = "${local.name_prefix}-lambda-exec"
  bedrock_role_name = "${local.name_prefix}-bedrock-scoped"
}

# ─── Lambda Execution Role ────────────────────────────────────────────────────

resource "aws_iam_role" "lambda_exec" {
  name = local.lambda_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.name_prefix}-*:*"
    }]
  })
}

# STS: Lambda może assumować wyłącznie rolę bedrock_scoped i przekazywać session tags
resource "aws_iam_role_policy" "lambda_sts_assume_bedrock" {
  name = "sts-assume-bedrock-scoped-role"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sts:AssumeRole", "sts:TagSession"]
      Resource = aws_iam_role.bedrock_scoped.arn
    }]
  })
}

# DynamoDB: dostęp tylko do tabel tego projektu
resource "aws_iam_role_policy" "lambda_dynamodb" {
  name = "dynamodb-project-tables"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
      ]
      Resource = [
        "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${local.name_prefix}-sessions",
        "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${local.name_prefix}-sessions/index/*",
        "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${local.name_prefix}-conversation-history",
        "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${local.name_prefix}-conversation-history/index/*",
        "arn:aws:dynamodb:${local.region}:${local.account_id}:table/${local.name_prefix}-revoked-tokens",
      ]
    }]
  })
}

# ─── Bedrock Scoped Role (assumed by Lambda via STS) ─────────────────────────
# ABAC: wymagane session tags department + clearance_level (ustawiane przez Lambda z JWT).
# Hierarchia clearance (NumericLessThanEquals) jest egzekwowana przez aplikację
# (Bedrock metadataFilter) – Bedrock nie wspiera resource-tag conditions na Retrieve.

resource "aws_iam_role" "bedrock_scoped" {
  name = local.bedrock_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.lambda_exec.arn }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      # Both tags must be present in the assume-role call (defense-in-depth).
      # Permissions policy enforces they're non-null at request time too.
      Condition = {
        Null = {
          "aws:RequestTag/department"      = "false"
          "aws:RequestTag/clearance_level" = "false"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_scoped_permissions" {
  name = "bedrock-retrieve-and-generate"
  role = aws_iam_role.bedrock_scoped.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Null: "false" = tag MUSI być obecny; brak tagu = deny
        Sid    = "BedrockRetrieveRequireSessionTags"
        Effect = "Allow"
        Action = [
          "bedrock:RetrieveAndGenerate",
          "bedrock:Retrieve",
        ]
        Resource = "*"
        Condition = {
          Null = {
            "aws:PrincipalTag/department"      = "false"
            "aws:PrincipalTag/clearance_level" = "false"
          }
          StringEquals = { "aws:RequestedRegion" = var.aws_region }
        }
      },
      {
        Sid      = "BedrockInvokeModelRequireSessionTags"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/${var.bedrock_model_id}"
        Condition = {
          Null = {
            "aws:PrincipalTag/department"      = "false"
            "aws:PrincipalTag/clearance_level" = "false"
          }
        }
      },
    ]
  })
}
