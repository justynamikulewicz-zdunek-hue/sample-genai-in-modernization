output "function_name" {
  value = aws_lambda_function.app.function_name
}

output "function_arn" {
  value = aws_lambda_function.app.arn
}

# Trailing slash stripped so callers can append paths without doubling it.
output "app_url" {
  description = "Public HTTPS URL of the app. AWS-issued certificate, unlike the ALB's self-signed one."
  value       = trimsuffix(aws_lambda_function_url.app.function_url, "/")
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
