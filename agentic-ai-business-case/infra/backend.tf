terraform {
  # Partial backend config — key is supplied per-client at init time:
  #   tofu init -backend-config=backends/<client>.hcl
  backend "s3" {
    bucket         = "map-accelerator-tfstate-680696743786"
    region         = "eu-north-1"
    dynamodb_table = "terraform-state-lock"
    encrypt        = true
  }
}
