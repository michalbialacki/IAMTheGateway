# ─── IAM Role – least-privilege for Revoke Lambda ────────────────────────────
# Only PutItem on revoked_tokens; no read/update/delete permissions.

resource "aws_iam_role" "lambda_revoke" {
  name = "${local.name_prefix}-revoke-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "revoke_basic_execution" {
  role       = aws_iam_role.lambda_revoke.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "revoke_dynamodb_write" {
  name = "revoked-tokens-write"
  role = aws_iam_role.lambda_revoke.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem"]
      Resource = aws_dynamodb_table.revoked_tokens.arn
    }]
  })
}

# ─── Lambda Function ─────────────────────────────────────────────────────────

data "archive_file" "revoke_handler" {
  type        = "zip"
  source_file = "${path.module}/../lambda/revoke/handler.py"
  output_path = "${path.module}/../lambda/revoke/handler.zip"
}

resource "aws_lambda_function" "revoke" {
  filename         = data.archive_file.revoke_handler.output_path
  function_name    = "${local.name_prefix}-revoke"
  role             = aws_iam_role.lambda_revoke.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  source_code_hash = data.archive_file.revoke_handler.output_base64sha256
  timeout          = 10

  environment {
    variables = {
      REVOKED_TOKENS_TABLE = aws_dynamodb_table.revoked_tokens.name
    }
  }

  depends_on = [aws_iam_role_policy_attachment.revoke_basic_execution]
}

# Allow API Gateway to invoke this Lambda directly (no IAM role needed for AWS_PROXY).
resource "aws_lambda_permission" "apigw_invoke_revoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.revoke.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─── API Gateway: POST /revoke ────────────────────────────────────────────────

resource "aws_api_gateway_resource" "revoke" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "revoke"
}

resource "aws_api_gateway_method" "revoke_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.revoke.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.jwt.id

  request_parameters = {
    "method.request.header.Authorization" = true
  }
}

resource "aws_api_gateway_integration" "revoke_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.revoke.id
  http_method             = aws_api_gateway_method.revoke_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.revoke.invoke_arn
}
