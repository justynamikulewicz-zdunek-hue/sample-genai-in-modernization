variable "client_name" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "target_group_arn" {
  type = string
}

variable "task_execution_role_arn" {
  type = string
}

variable "task_role_arn" {
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

variable "cognito_user_pool_id" {
  type = string
}

variable "cognito_client_id" {
  type = string
}

variable "cognito_client_secret" {
  type      = string
  sensitive = true
}

variable "cognito_domain" {
  type = string
}

variable "app_url" {
  type = string
}

variable "container_cpu" {
  type    = number
  default = 2048
}

variable "container_memory" {
  type    = number
  default = 4096
}
