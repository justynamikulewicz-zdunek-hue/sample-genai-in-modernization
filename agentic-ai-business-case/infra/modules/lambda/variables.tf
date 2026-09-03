variable "client_name" {
  type = string
}

variable "lambda_role_arn" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "s3_input_bucket" {
  type = string
}

variable "s3_output_bucket" {
  type = string
}

variable "dynamodb_table_name" {
  type = string
}

variable "cognito_ssm_prefix" {
  type        = string
  description = "SSM path holding Cognito config, read by the app at cold start. Passing the values directly would make the Terraform graph circular."
}

# --- Where long generations actually run -------------------------------------
variable "ecs_cluster_arn" {
  type = string
}

variable "ecs_task_definition_family" {
  type        = string
  description = "Family without revision, so RunTask always picks the newest."
}

variable "ecs_container_name" {
  type = string
}

variable "ecs_subnet_ids" {
  type        = list(string)
  description = "Public subnets. The task needs internet egress for Bedrock and ECR, and a public IP is cheaper than a NAT Gateway for occasional jobs."
}

variable "ecs_security_group_id" {
  type = string
}

variable "timeout_seconds" {
  type        = number
  default     = 780
  description = "Lambda's hard ceiling is 900s and is not adjustable. Anything longer must go to ECS RunTask."
  validation {
    condition     = var.timeout_seconds > 0 && var.timeout_seconds <= 900
    error_message = "timeout_seconds must be between 1 and 900 (AWS hard limit)."
  }
}

variable "memory_mb" {
  type        = number
  default     = 4096
  description = "Lambda allocates CPU in proportion to memory; 1769 MB equals one vCPU. 4096 approximates the 2 vCPU the Fargate task had."
  validation {
    condition     = var.memory_mb >= 128 && var.memory_mb <= 10240
    error_message = "memory_mb must be between 128 and 10240."
  }
}

variable "tmp_storage_mb" {
  type        = number
  default     = 2048
  description = "Size of /tmp. Uploaded RVTools files and generated Excel reports land here."
  validation {
    condition     = var.tmp_storage_mb >= 512 && var.tmp_storage_mb <= 10240
    error_message = "tmp_storage_mb must be between 512 and 10240."
  }
}
