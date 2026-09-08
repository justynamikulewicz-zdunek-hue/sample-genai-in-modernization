variable "client_name" {
  type = string
}

variable "lambda_invoke_arn" {
  type        = string
  description = "invoke_arn of the app function (not its plain ARN) — this is what an AWS_PROXY integration takes."
}

variable "lambda_function_name" {
  type        = string
  description = "Function the API is granted permission to invoke."
}
