output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "client_id" {
  value = aws_cognito_user_pool_client.app.id
}

# Replaces CF Lambda GetClientSecretLambda — available as direct Terraform attribute
output "client_secret" {
  value     = aws_cognito_user_pool_client.app.client_secret
  sensitive = true
}

output "domain" {
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
}
