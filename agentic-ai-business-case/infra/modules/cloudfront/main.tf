# ---------------------------------------------------------------------------
# CloudFront in front of the Lambda Function URL.
#
# The function URL is AuthType AWS_IAM, so it cannot be called anonymously.
# Origin Access Control makes CloudFront SigV4-sign every request it forwards,
# which is what lets viewers reach the app without the URL being public.
#
# This exists because anonymous function URLs are blocked by organisation
# policy, but it earns its place regardless: it gives a stable domain, a place
# to attach WAF later, and access logs the raw function URL never had.
# ---------------------------------------------------------------------------
resource "aws_cloudfront_origin_access_control" "app" {
  name                              = "${var.client_name}-lambda-oac"
  description                       = "Signs CloudFront requests to the ${var.client_name} Lambda Function URL"
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Managed policies, looked up by name rather than hardcoded id.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Forwards everything except Host. Critical for a Lambda Function URL origin:
# the origin must receive its own hostname, or SigV4 verification fails.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  comment         = "${var.client_name} business case generator"
  is_ipv6_enabled = true

  # Europe and North America only — the users are in the EU and the wider
  # edge footprint costs more for no benefit here.
  price_class = "PriceClass_100"

  origin {
    domain_name              = var.function_url_domain
    origin_id                = "lambda"
    origin_access_control_id = aws_cloudfront_origin_access_control.app.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]

      # Generation is queued and returns immediately now, but page loads still
      # pull a sizeable React bundle through here on a cold start.
      origin_read_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = "lambda"
    viewer_protocol_policy = "redirect-to-https"

    # The app is dynamic and session-bound; POST and friends must pass through.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    # Nothing is cached. Responses are per-user and carry session cookies;
    # caching them would serve one user's page to another.
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

    compress = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # *.cloudfront.net certificate. Swap for an ACM cert when a custom domain
    # is attached.
    cloudfront_default_certificate = true
  }

  tags = { Name = "${var.client_name}-cloudfront" }
}

# Only this distribution may invoke the function URL.
resource "aws_lambda_permission" "cloudfront" {
  statement_id           = "AllowCloudFrontInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = var.function_name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = aws_cloudfront_distribution.app.arn
  function_url_auth_type = "AWS_IAM"
}
