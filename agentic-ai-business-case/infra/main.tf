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

module "alb" {
  source                = "./modules/alb"
  client_name           = var.client_name
  vpc_id                = module.vpc.vpc_id
  public_subnet_ids     = module.vpc.public_subnet_ids
  alb_security_group_id = module.vpc.alb_security_group_id
}

module "cognito" {
  source                = "./modules/cognito"
  client_name           = var.client_name
  admin_email           = var.admin_email
  aws_region            = var.aws_region
  alb_dns_name          = module.alb.alb_dns_name
  cognito_domain_suffix = random_id.cognito_suffix.hex
}

module "ecs" {
  source                  = "./modules/ecs"
  client_name             = var.client_name
  aws_region              = var.aws_region
  vpc_id                  = module.vpc.vpc_id
  private_subnet_ids      = module.vpc.private_subnet_ids
  ecs_security_group_id   = module.vpc.ecs_security_group_id
  target_group_arn        = module.alb.target_group_arn
  task_execution_role_arn = module.iam.ecs_task_execution_role_arn
  task_role_arn           = module.iam.ecs_task_role_arn
  ecr_repository_url      = module.ecr.repository_url
  s3_input_bucket         = module.s3.input_bucket_name
  s3_output_bucket        = module.s3.output_bucket_name
  dynamodb_table_name     = module.dynamodb.table_name
  cognito_user_pool_id    = module.cognito.user_pool_id
  cognito_client_id       = module.cognito.client_id
  cognito_client_secret   = module.cognito.client_secret
  cognito_domain          = module.cognito.domain
  app_url                 = "https://${module.alb.alb_dns_name}"
  container_cpu           = var.container_cpu
  container_memory        = var.container_memory
}

module "codebuild" {
  source             = "./modules/codebuild"
  client_name        = var.client_name
  aws_region         = var.aws_region
  account_id         = data.aws_caller_identity.current.account_id
  ecr_repository_url = module.ecr.repository_url
  codebuild_role_arn = module.iam.codebuild_role_arn
  github_repo_url    = var.github_repo_url
  github_branch      = var.github_branch
  project_subdir     = var.project_subdir
}
