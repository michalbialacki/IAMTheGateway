locals {
  layer_zip_path = "${path.module}/../lambda/authorizer/layer.zip"
}

# ─── IAM Role – least-privilege for Authorizer ───────────────────────────────
# Separate role from lambda_exec: no STS/Bedrock permissions needed here.

resource "aws_iam_role" "lambda_authorizer" {
  name = "${local.name_prefix}-authorizer-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "authorizer_basic_execution" {
  role       = aws_iam_role.lambda_authorizer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "authorizer_revoked_tokens_read" {
  name = "revoked-tokens-read"
  role = aws_iam_role.lambda_authorizer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem"]
      Resource = aws_dynamodb_table.revoked_tokens.arn
    }]
  })
}

# ─── Lambda Layer (PyJWT + cryptography) ─────────────────────────────────────
# Built by: python scripts/build_authorizer_layer.py
# Must exist before `terraform plan`. Layer skipped if zip is missing (validate-safe).

resource "aws_lambda_layer_version" "authorizer_deps" {
  count               = fileexists(local.layer_zip_path) ? 1 : 0
  filename            = local.layer_zip_path
  layer_name          = "${local.name_prefix}-authorizer-deps"
  compatible_runtimes = ["python3.12"]
  source_code_hash    = try(filebase64sha256(local.layer_zip_path), null)
}

# ─── Lambda Function ──────────────────────────────────────────────────────────

data "archive_file" "authorizer_handler" {
  type        = "zip"
  source_file = "${path.module}/../lambda/authorizer/handler.py"
  output_path = "${path.module}/../lambda/authorizer/handler.zip"
}

resource "aws_lambda_function" "authorizer" {
  filename         = data.archive_file.authorizer_handler.output_path
  function_name    = "${local.name_prefix}-authorizer"
  role             = aws_iam_role.lambda_authorizer.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  source_code_hash = data.archive_file.authorizer_handler.output_base64sha256
  timeout          = 10

  # Splat returns [] when layer count=0 (layer not yet built).
  layers = aws_lambda_layer_version.authorizer_deps[*].arn

  environment {
    variables = {
      JWKS_URI             = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/jwks.json"
      USER_POOL_ID         = aws_cognito_user_pool.main.id
      REVOKED_TOKENS_TABLE = aws_dynamodb_table.revoked_tokens.name
    }
  }

  depends_on = [aws_iam_role_policy_attachment.authorizer_basic_execution]
}
