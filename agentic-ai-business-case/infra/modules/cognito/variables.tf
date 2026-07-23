variable "client_name" {
  type = string
}

variable "admin_email" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "alb_dns_name" {
  type = string
}

variable "cognito_domain_suffix" {
  type        = string
  description = "Random hex suffix to ensure globally unique Cognito domain"
}
