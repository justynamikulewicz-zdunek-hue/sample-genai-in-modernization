# Trailing slash stripped: callers append paths like /auth/callback, and Cognito
# rejects a callback URL containing a double slash.
output "app_url" {
  description = "Public URL of the application. AWS-issued certificate; this is what Cognito callbacks point at."
  value       = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
}

output "api_id" {
  value = aws_apigatewayv2_api.app.id
}
