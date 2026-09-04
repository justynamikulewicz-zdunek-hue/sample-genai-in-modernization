variable "client_name" {
  type = string
}

variable "function_url_domain" {
  type        = string
  description = "Hostname of the Lambda Function URL used as the origin (no scheme, no trailing slash)."
}

variable "function_name" {
  type        = string
  description = "Function the distribution is allowed to invoke."
}
