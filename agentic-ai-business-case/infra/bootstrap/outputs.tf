output "state_bucket_name" {
  value = aws_s3_bucket.tfstate.bucket
}

output "lock_table_name" {
  value = aws_dynamodb_table.tflock.name
}

output "terraform_deployer_role_arn" {
  value = aws_iam_role.terraform_deployer.arn
}

output "github_oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}
