locals {
  api_rate_limit  = 100
  api_burst_limit = 50
}

# ─── REST API ─────────────────────────────────────────────────────────────────

resource "aws_api_gateway_rest_api" "main" {
  name = "${local.name_prefix}-api"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# ─── IAM role: allows API Gateway to invoke the Lambda Authorizer ─────────────

resource "aws_iam_role" "apigw_authorizer_invoke" {
  name = "${local.name_prefix}-apigw-authorizer-invoke"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apigw_invoke_authorizer" {
  name = "invoke-authorizer-lambda"
  role = aws_iam_role.apigw_authorizer_invoke.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.authorizer.arn
    }]
  })
}

# ─── Lambda Authorizer (TOKEN type) ──────────────────────────────────────────
# TTL=0: authorizer is called on every request so revocation takes effect immediately.

resource "aws_api_gateway_authorizer" "jwt" {
  name                             = "jwt-authorizer"
  rest_api_id                      = aws_api_gateway_rest_api.main.id
  authorizer_uri                   = aws_lambda_function.authorizer.invoke_arn
  authorizer_credentials           = aws_iam_role.apigw_authorizer_invoke.arn
  type                             = "TOKEN"
  identity_source                  = "method.request.header.Authorization"
  authorizer_result_ttl_in_seconds = 0
}

# ─── /chat resource + POST method ────────────────────────────────────────────

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "chat"
}

resource "aws_api_gateway_method" "chat_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.chat.id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.jwt.id

  request_parameters = {
    "method.request.header.Authorization" = true
  }
}

# ─── MOCK integration (placeholder – replaced with Lambda in Phase 03) ────────

resource "aws_api_gateway_integration" "chat_mock" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_post.http_method
  type        = "MOCK"

  request_templates = {
    "application/json" = jsonencode({ statusCode = 200 })
  }
}

resource "aws_api_gateway_method_response" "chat_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_post.http_method
  status_code = "200"

  response_models = {
    "application/json" = "Empty"
  }
}

resource "aws_api_gateway_integration_response" "chat_mock" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.chat.id
  http_method = aws_api_gateway_method.chat_post.http_method
  status_code = aws_api_gateway_method_response.chat_200.status_code

  response_templates = {
    "application/json" = jsonencode({ message = "ok" })
  }

  depends_on = [aws_api_gateway_integration.chat_mock]
}

# ─── Deployment + Stage ───────────────────────────────────────────────────────

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.chat.id,
      aws_api_gateway_method.chat_post.id,
      aws_api_gateway_integration.chat_mock.id,
      aws_api_gateway_resource.revoke.id,
      aws_api_gateway_method.revoke_post.id,
      aws_api_gateway_integration.revoke_post.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_method.chat_post,
    aws_api_gateway_integration.chat_mock,
    aws_api_gateway_integration_response.chat_mock,
    aws_api_gateway_method.revoke_post,
    aws_api_gateway_integration.revoke_post,
  ]
}

resource "aws_api_gateway_stage" "prod" {
  deployment_id = aws_api_gateway_deployment.main.id
  rest_api_id   = aws_api_gateway_rest_api.main.id
  stage_name    = "prod"
}

resource "aws_api_gateway_method_settings" "prod_throttle" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  stage_name  = aws_api_gateway_stage.prod.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = local.api_rate_limit
    throttling_burst_limit = local.api_burst_limit
  }
}
