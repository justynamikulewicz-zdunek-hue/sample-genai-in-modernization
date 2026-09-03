variable "client_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "codebuild_role_arn" {
  type = string
}

variable "lambda_function_name" {
  type        = string
  description = "Function the build rolls the new image onto. Pushing to ECR alone does not update a Lambda."
}

variable "github_repo_url" {
  type    = string
  default = "https://github.com/justynamikulewicz-zdunek-hue/sample-genai-in-modernization"
}

variable "github_branch" {
  type    = string
  default = "main"
}

variable "project_subdir" {
  type    = string
  default = "agentic-ai-business-case"
}
