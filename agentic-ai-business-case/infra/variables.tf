variable "client_name" {
  type        = string
  description = "Unique client identifier. All resources will be prefixed with this value."
  validation {
    condition     = can(regex("^[a-z0-9-]{3,20}$", var.client_name))
    error_message = "client_name must be 3-20 lowercase alphanumeric characters or hyphens."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region. Must support Bedrock Claude 3 Sonnet."
  default     = "eu-west-1"
  validation {
    condition = contains([
      "us-east-1", "us-west-2", "eu-west-1", "eu-north-1",
      "eu-central-1", "ap-southeast-1", "ap-northeast-1"
    ], var.aws_region)
    error_message = "Region not in supported list."
  }
}

variable "aws_profile" {
  type    = string
  default = "stxnext-devops"
}

variable "admin_email" {
  type        = string
  description = "Email of the initial Cognito admin user. Will receive a temporary password."
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "container_cpu" {
  type    = number
  default = 2048
  validation {
    condition     = contains([512, 1024, 2048, 4096], var.container_cpu)
    error_message = "container_cpu must be one of: 512, 1024, 2048, 4096."
  }
}

variable "container_memory" {
  type    = number
  default = 4096
  validation {
    condition     = contains([1024, 2048, 4096, 8192], var.container_memory)
    error_message = "container_memory must be one of: 1024, 2048, 4096, 8192."
  }
}

variable "lambda_memory_mb" {
  type        = number
  default     = 4096
  description = "Memory for the web-tier Lambda. CPU scales with it (1769 MB = 1 vCPU), so 4096 roughly matches the 2 vCPU the Fargate task had."
}

variable "lambda_timeout_seconds" {
  type        = number
  default     = 780
  description = "Below AWS's non-adjustable 900s ceiling, leaving headroom. Generations expected to run longer are handed to ECS RunTask."
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
