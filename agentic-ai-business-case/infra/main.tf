terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "map-agentic-accelerator"
      Client    = var.client_name
      ManagedBy = "opentofu"
    }
  }
}

data "aws_caller_identity" "current" {}

# Unique suffix for Cognito domain (stable across applies for same account)
resource "random_id" "cognito_suffix" {
  keepers     = { account_id = data.aws_caller_identity.current.account_id }
  byte_length = 4
}

# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------
module "vpc" {
  source      = "./modules/vpc"
  client_name = var.client_name
  vpc_cidr    = var.vpc_cidr
  aws_region  = var.aws_region
}

module "ecr" {
  source      = "./modules/ecr"
  client_name = var.client_name
}

module "s3" {
  source      = "./modules/s3"
  client_name = var.client_name
  account_id  = data.aws_caller_identity.current.account_id
  aws_region  = var.aws_region
}

module "dynamodb" {
  source      = "./modules/dynamodb"
  client_name = var.client_name
}

module "iam" {
  source               = "./modules/iam"
  client_name          = var.client_name
  s3_input_bucket_arn  = module.s3.input_bucket_arn
  s3_output_bucket_arn = module.s3.output_bucket_arn
  dynamodb_table_arn   = module.dynamodb.table_arn
  ecr_repository_arn   = module.ecr.repository_arn
}

# The ALB module is gone: a Lambda Function URL serves the app directly, with an
# AWS-issued certificate instead of the ALB's self-signed one. The module
# directory stays in the repo for clients who mandate an ALB.

# ---------------------------------------------------------------------------
# Dependency order matters below.
#
# The app's public URL now belongs to the Lambda itself, so passing Cognito IDs
# into the function would close a loop:
#     lambda -> function_url -> cognito.callback_urls -> lambda.environment
# The chain is straightened by publishing Cognito config to SSM and having the
# app read it at cold start (see ui/backend/cognito_auth.py):
#     lambda -> apigateway -> cognito -> SSM
# ---------------------------------------------------------------------------
locals {
  cognito_ssm_prefix = "/${var.client_name}/cognito"
}

module "lambda" {
  source              = "./modules/lambda"
  client_name         = var.client_name
  lambda_role_arn     = module.iam.lambda_role_arn
  ecr_repository_url  = module.ecr.repository_url
  s3_input_bucket     = module.s3.input_bucket_name
  s3_output_bucket    = module.s3.output_bucket_name
  dynamodb_table_name = module.dynamodb.table_name
  cognito_ssm_prefix  = local.cognito_ssm_prefix
  memory_mb           = var.lambda_memory_mb
  timeout_seconds     = var.lambda_timeout_seconds

  # Generation jobs run here, not in the function itself.
  ecs_cluster_arn            = module.ecs.cluster_arn
  ecs_task_definition_family = module.ecs.task_definition_family
  ecs_container_name         = module.ecs.container_name
  ecs_subnet_ids             = module.vpc.public_subnet_ids
  ecs_security_group_id      = module.vpc.ecs_security_group_id
}

# The app's entry point. Lambda Function URLs are unusable on this account —
# organisation policy denies lambda:InvokeFunctionUrl for every caller, so both
# an anonymous URL and a CloudFront-signed one returned 403. An HTTP API
# invokes through lambda:InvokeFunction instead, which is permitted.
#
# The cloudfront module stays in the repo, unwired, for the day that guardrail
# changes or a custom domain and WAF are wanted in front of this API.
module "apigateway" {
  source               = "./modules/apigateway"
  client_name          = var.client_name
  lambda_invoke_arn    = module.lambda.invoke_arn
  lambda_function_name = module.lambda.function_name
}

module "cognito" {
  source                = "./modules/cognito"
  client_name           = var.client_name
  admin_email           = var.admin_email
  aws_region            = var.aws_region
  app_url               = module.apigateway.app_url
  ssm_prefix            = local.cognito_ssm_prefix
  cognito_domain_suffix = random_id.cognito_suffix.hex
}

# Cluster and task definition only — no service. The Lambda uses these to run
# generations that would exceed its 900s ceiling. Costs nothing while idle.
module "ecs" {
  source                  = "./modules/ecs"
  client_name             = var.client_name
  aws_region              = var.aws_region
  task_execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn           = module.iam.ecs_task_role_arn
  ecr_repository_url      = module.ecr.repository_url
  s3_input_bucket         = module.s3.input_bucket_name
  s3_output_bucket        = module.s3.output_bucket_name
  dynamodb_table_name     = module.dynamodb.table_name
  container_cpu           = var.container_cpu
  container_memory        = var.container_memory
}

module "codebuild" {
  source               = "./modules/codebuild"
  client_name          = var.client_name
  aws_region           = var.aws_region
  account_id           = data.aws_caller_identity.current.account_id
  ecr_repository_url   = module.ecr.repository_url
  codebuild_role_arn   = module.iam.codebuild_role_arn
  lambda_function_name = module.lambda.function_name
  github_repo_url      = var.github_repo_url
  github_branch        = var.github_branch
  project_subdir       = var.project_subdir
}
