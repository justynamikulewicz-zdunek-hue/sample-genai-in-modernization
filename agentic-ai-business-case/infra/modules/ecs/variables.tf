variable "client_name" {
  type = string
}

variable "aws_region" {
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

variable "container_cpu" {
  type        = number
  default     = 2048
  description = "vCPU units for the one-shot generation task. Only billed while a task runs."
}

variable "container_memory" {
  type    = number
  default = 4096
}
