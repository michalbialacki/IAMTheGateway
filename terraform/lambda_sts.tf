# ─── Lambda STS Session Handler ──────────────────────────────────────────────
# Phase 03: assumes bedrock_scoped role with ABAC session tags drawn from the
# JWT authorizer context (user_id, department, clearance_level, jti).
# API Gateway integration (replacing MOCK) wired in Phase 03 Step 02.

data "archive_file" "sts_handler" {
  type        = "zip"
  output_path = "${path.module}/../lambda/sts/handler.zip"

  # handler.py + sanitizer package bundled together so Lambda can import it
  source {
    content  = file("${path.module}/../lambda/sts/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${path.module}/../lambda/sanitizer/__init__.py")
    filename = "sanitizer/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambda/sanitizer/patterns.py")
    filename = "sanitizer/patterns.py"
  }
  source {
    content  = file("${path.module}/../lambda/sanitizer/sanitizer.py")
    filename = "sanitizer/sanitizer.py"
  }
  source {
    content  = file("${path.module}/../lambda/sanitizer/sandwich.py")
    filename = "sanitizer/sandwich.py"
  }
  source {
    content  = file("${path.module}/../lambda/sanitizer/policy.py")
    filename = "sanitizer/policy.py"
  }
}

resource "aws_lambda_function" "sts_session" {
  filename         = data.archive_file.sts_handler.output_path
  function_name    = "${local.name_prefix}-sts-session"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  source_code_hash = data.archive_file.sts_handler.output_base64sha256
  timeout          = 15

  environment {
    variables = {
      BEDROCK_ROLE_ARN = aws_iam_role.bedrock_scoped.arn
      BEDROCK_MODEL_ID = var.bedrock_model_id
    }
  }
}

# Allows API Gateway to invoke the Lambda after wiring in Step 02.
resource "aws_lambda_permission" "apigw_invoke_sts" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sts_session.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

output "sts_lambda_arn" {
  value       = aws_lambda_function.sts_session.arn
  description = "ARN of the STS session handler Lambda"
}
