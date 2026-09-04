output "app_url" {
  description = "Public URL of the application. This is what users and Cognito callbacks point at."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
}

output "distribution_id" {
  value = aws_cloudfront_distribution.app.id
}

output "domain_name" {
  value = aws_cloudfront_distribution.app.domain_name
}
