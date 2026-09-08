# ---------------------------------------------------------------------------
# HTTP API in front of the Lambda.
#
# Replaces the Lambda Function URL, which could not be reached from the
# internet at all: the account belongs to an AWS Organization whose policy
# denies lambda:InvokeFunctionUrl. Anonymous calls were refused, and so were
# SigV4-signed calls from CloudFront Origin Access Control, even though the
# function's resource policy was correct in both cases.
#
# API Gateway invokes through lambda:InvokeFunction instead — a different
# action, and one demonstrably permitted here (a direct `aws lambda invoke`
# returned 200 from the app while every function URL request returned 403).
#
# A single $default route proxies everything to the app, which already serves
# both the React bundle and the API from one Flask process.
# ---------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "app" {
  name          = "${var.client_name}-business-case-api"
  description   = "Public entry point for the ${var.client_name} business case generator"
  protocol_type = "HTTP"

  # No CORS configuration: the frontend and the API share this origin.

  tags = { Name = "${var.client_name}-business-case-api" }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id           = aws_apigatewayv2_api.app.id
  integration_type = "AWS_PROXY"
  integration_uri  = var.lambda_invoke_arn

  # 2.0 is what the Lambda Web Adapter expects from an HTTP API.
  payload_format_version = "2.0"

  # HTTP APIs cap integrations at 30s. That is comfortable now that report
  # generation is queued to a Fargate task rather than served inline — the
  # slowest remaining request is a cold start pulling the container image.
  timeout_milliseconds = 30000
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.app.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# $default stage means the URL carries no stage prefix, so paths the app
# already serves (/api/health, /auth/callback) work unchanged.
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    # Generous enough not to interfere with a demo, low enough that a runaway
    # client cannot drive up Bedrock spend unnoticed.
    throttling_burst_limit = 100
    throttling_rate_limit  = 50
  }

  tags = { Name = "${var.client_name}-business-case-api" }
}

resource "aws_lambda_permission" "apigateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}
