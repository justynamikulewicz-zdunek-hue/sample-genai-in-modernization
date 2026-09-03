output "app_url" {
  description = "Application URL (Lambda Function URL). AWS-issued certificate — no browser warning, unlike the previous self-signed ALB."
  value       = module.lambda.app_url
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = module.cognito.domain
}

output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

output "ecr_repository_url" {
  description = "ECR URL — CodeBuild pushes the image here"
  value       = module.ecr.repository_url
}

output "codebuild_project_name" {
  description = "Trigger this project to build the image and roll it onto the Lambda"
  value       = module.codebuild.project_name
}

output "lambda_function_name" {
  value = module.lambda.function_name
}

output "lambda_log_group" {
  description = "Where to look when the app misbehaves"
  value       = module.lambda.log_group_name
}

output "ecs_cluster_name" {
  description = "Runs one-shot generation jobs only. There is no long-running service any more."
  value       = module.ecs.cluster_name
}

output "s3_input_bucket" {
  value = module.s3.input_bucket_name
}

output "s3_output_bucket" {
  value = module.s3.output_bucket_name
}

output "dynamodb_table_name" {
  value = module.dynamodb.table_name
}

output "next_steps" {
  value = <<-EOT
    =============================================================
    DEPLOYMENT COMPLETE — NEXT STEPS
    =============================================================
    1. Enable Bedrock model access (if not done):
       AWS Console -> Bedrock -> Model catalog -> Claude Sonnet 4.5 -> Submit use case details
       (one-time per account; eu-* regions use the eu. cross-region inference profile)
       Region: ${var.aws_region}

    2. Build the image and roll it onto the Lambda:
       aws codebuild start-build \
         --project-name ${module.codebuild.project_name} \
         --profile ${var.aws_profile}

       The build now also calls UpdateFunctionCode. Pushing to ECR alone does
       NOT update a Lambda — the function pins the image digest at deploy time.

    3. Monitor build:
       https://${var.aws_region}.console.aws.amazon.com/codesuite/codebuild/projects/${module.codebuild.project_name}/history

    4. Access the app (~1 min after build):
       ${module.lambda.app_url}
       No certificate warning any more — this is an AWS-issued cert.

       First request after a deploy is a cold start (container image, several
       hundred MB) and may take 10-20s. Subsequent requests are warm.
    =============================================================
  EOT
}
