# Outputs populated incrementally as resources are added each step.

output "aws_region" {
  description = "Deployed AWS region"
  value       = local.region
}

output "aws_account_id" {
  description = "AWS account ID"
  value       = local.account_id
}

output "lambda_exec_role_arn" {
  description = "ARN of the Lambda execution role"
  value       = aws_iam_role.lambda_exec.arn
}

output "bedrock_scoped_role_arn" {
  description = "ARN of the Bedrock scoped role (assumed via STS)"
  value       = aws_iam_role.bedrock_scoped.arn
}

output "dynamodb_sessions_table" {
  description = "Name of the sessions DynamoDB table"
  value       = aws_dynamodb_table.sessions.name
}

output "dynamodb_conversation_history_table" {
  description = "Name of the conversation history DynamoDB table"
  value       = aws_dynamodb_table.conversation_history.name
}

output "dynamodb_revoked_tokens_table" {
  description = "Name of the revoked tokens DynamoDB table"
  value       = aws_dynamodb_table.revoked_tokens.name
}

output "knowledge_base_bucket_name" {
  description = "Name of the S3 bucket for Bedrock Knowledge Base documents"
  value       = aws_s3_bucket.knowledge_base.bucket
}

output "knowledge_base_bucket_arn" {
  description = "ARN of the S3 bucket for Bedrock Knowledge Base documents"
  value       = aws_s3_bucket.knowledge_base.arn
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_user_pool_arn" {
  description = "Cognito User Pool ARN"
  value       = aws_cognito_user_pool.main.arn
}

output "cognito_app_client_id" {
  description = "Cognito App Client ID (public – no secret)"
  value       = aws_cognito_user_pool_client.cli.id
}

output "cognito_jwks_uri" {
  description = "JWKS endpoint used by Lambda Authorizer to verify JWT signatures"
  value       = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/jwks.json"
}

output "cloudtrail_trail_arn" {
  description = "ARN of the CloudTrail trail"
  value       = aws_cloudtrail.main.arn
}

output "cloudtrail_log_group_name" {
  description = "CloudWatch Log Group name for CloudTrail events"
  value       = aws_cloudwatch_log_group.cloudtrail.name
}

output "authorizer_lambda_arn" {
  description = "ARN of the Lambda Authorizer function"
  value       = aws_lambda_function.authorizer.arn
}

output "authorizer_lambda_invoke_arn" {
  description = "Invoke ARN used in API Gateway integration"
  value       = aws_lambda_function.authorizer.invoke_arn
}

output "api_gateway_id" {
  description = "REST API ID"
  value       = aws_api_gateway_rest_api.main.id
}

output "api_gateway_endpoint" {
  description = "Base URL for the prod stage (no trailing slash)"
  value       = aws_api_gateway_stage.prod.invoke_url
}

output "chat_endpoint" {
  description = "Full URL for POST /chat"
  value       = "${aws_api_gateway_stage.prod.invoke_url}/chat"
}

output "knowledge_base_id" {
  description = "Bedrock Knowledge Base ID"
  value       = aws_bedrockagent_knowledge_base.main.id
}

output "knowledge_base_arn" {
  description = "Bedrock Knowledge Base ARN"
  value       = aws_bedrockagent_knowledge_base.main.arn
}

output "data_source_id" {
  description = "Bedrock Knowledge Base Data Source ID"
  value       = aws_bedrockagent_data_source.kb.data_source_id
}

output "aoss_collection_arn" {
  description = "OpenSearch Serverless collection ARN"
  value       = aws_opensearchserverless_collection.kb.arn
}

output "aoss_collection_endpoint" {
  description = "OpenSearch Serverless collection endpoint"
  value       = aws_opensearchserverless_collection.kb.collection_endpoint
}
