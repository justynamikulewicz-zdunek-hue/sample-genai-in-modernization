variable "aws_region" {
  type    = string
  default = "eu-north-1"
}

variable "aws_profile" {
  type    = string
  default = "stxnext-devops"
}

variable "account_id" {
  type        = string
  description = "AWS Account ID"
  default     = "680696743786"
}

variable "state_bucket_name" {
  type    = string
  default = "map-accelerator-tfstate-680696743786"
}

variable "lock_table_name" {
  type    = string
  default = "terraform-state-lock"
}

variable "github_repo" {
  type        = string
  description = "GitHub repo in owner/repo format"
  default     = "justynamikulewicz-zdunek-hue/sample-genai-in-modernization"
}
