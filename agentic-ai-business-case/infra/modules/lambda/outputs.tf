output "function_name" {
  value = aws_lambda_function.app.function_name
}

output "function_arn" {
  value = aws_lambda_function.app.arn
}

# Host only — CloudFront wants a domain name for its origin, not a URL.
output "function_url_domain" {
  description = "Hostname of the Function URL, for use as a CloudFront origin."
  value       = replace(replace(aws_lambda_function_url.app.function_url, "https://", ""), "/", "")
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
