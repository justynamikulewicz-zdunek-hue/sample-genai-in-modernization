output "function_name" {
  value = aws_lambda_function.app.function_name
}

output "function_arn" {
  value = aws_lambda_function.app.arn
}

# An AWS_PROXY integration takes invoke_arn, not the plain function ARN.
output "invoke_arn" {
  value = aws_lambda_function.app.invoke_arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
