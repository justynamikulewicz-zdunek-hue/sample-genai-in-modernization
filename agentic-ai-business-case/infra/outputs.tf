output "alb_url" {
  description = "Application URL. Note: self-signed cert, browser will warn — expected for PoC."
  value       = "https://${module.alb.alb_dns_name}"
}

output "cognito_domain" {
  description = "Cognito hosted UI domain"
  value       = module.cognito.domain
}

output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

output "ecr_repository_url" {
  description = "ECR URL — push Docker image here before ECS can start"
  value       = module.ecr.repository_url
}

output "codebuild_project_name" {
  description = "Trigger this project to build and push the Docker image"
  value       = module.codebuild.project_name
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "ecs_service_name" {
  value = module.ecs.service_name
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

    2. Build & push Docker image:
       aws codebuild start-build \
         --project-name ${module.codebuild.project_name} \
         --profile ${var.aws_profile}

    3. Monitor build:
       https://${var.aws_region}.console.aws.amazon.com/codesuite/codebuild/projects/${module.codebuild.project_name}/history

    4. Access the app (~5 min after build):
       https://${module.alb.alb_dns_name}
       (Accept self-signed cert warning)
    =============================================================
  EOT
}
