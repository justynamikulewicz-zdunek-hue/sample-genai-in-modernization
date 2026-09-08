locals {
  input_bucket_name  = "${var.client_name}-bc-input-${var.account_id}-${var.aws_region}"
  output_bucket_name = "${var.client_name}-bc-output-${var.account_id}-${var.aws_region}"
}

resource "aws_s3_bucket" "input" {
  bucket        = local.input_bucket_name
  force_destroy = true

  tags = { Name = "${var.client_name}-input", Purpose = "rvtools-uploads" }
}

resource "aws_s3_bucket_versioning" "input" {
  bucket = aws_s3_bucket.input.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "input" {
  bucket = aws_s3_bucket.input.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "input" {
  bucket                  = aws_s3_bucket.input.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Uploads go straight from the browser to S3 with a presigned URL, because the
# app runs on Lambda and a request body there is capped at 6 MB — smaller than
# a real RVTools export. That PUT is cross-origin, so without this rule the
# browser refuses it before it ever leaves the machine.
#
# The origin is a wildcard because the app's own URL belongs to the API Gateway
# that sits downstream of this bucket in the dependency graph; naming it here
# would make the graph circular. That is safe: CORS is not an authorisation
# boundary. Access is granted by the presigned URL's signature, which is
# short-lived and issued only to a logged-in user, and every public access
# setting on this bucket is blocked above.
resource "aws_s3_bucket_cors_configuration" "input" {
  bucket = aws_s3_bucket.input.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket" "output" {
  bucket        = local.output_bucket_name
  force_destroy = true

  tags = { Name = "${var.client_name}-output", Purpose = "generated-reports" }
}

resource "aws_s3_bucket_versioning" "output" {
  bucket = aws_s3_bucket.output.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "output" {
  bucket = aws_s3_bucket.output.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "output" {
  bucket                  = aws_s3_bucket.output.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
