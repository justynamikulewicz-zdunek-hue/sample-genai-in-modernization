variable "client_name" {
  type = string
}

variable "admin_email" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "app_url" {
  type        = string
  description = "Public app URL (Lambda Function URL), without trailing slash. Used for OAuth callback/logout."
}

variable "ssm_prefix" {
  type        = string
  description = "SSM path prefix under which Cognito config is published for the app to read."
}

variable "cognito_domain_suffix" {
  type        = string
  description = "Random hex suffix to ensure globally unique Cognito domain"
}
