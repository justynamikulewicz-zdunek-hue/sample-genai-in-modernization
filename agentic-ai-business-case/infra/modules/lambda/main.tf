resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/${var.client_name}-business-case-app"
  retention_in_days = 7

  tags = { Name = "${var.client_name}-lambda-logs" }
}

# ---------------------------------------------------------------------------
# Lambda — the same container image the ECS task uses, fronted by the AWS
# Lambda Web Adapter baked into the image. Gunicorn and the Flask app run
# unmodified; the adapter translates Lambda invocations into HTTP.
#
# Deliberately NOT attached to the VPC: Bedrock, S3, DynamoDB and SSM are all
# public AWS endpoints, so staying outside removes the NAT Gateway entirely
# (~$23/month, measured) and avoids cold-start ENI attachment.
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "app" {
  function_name = "${var.client_name}-business-case-app"
  role          = var.lambda_role_arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repository_url}:latest"

  # CodeBuild image is amazonlinux2-x86_64, so the function must match.
  architectures = ["x86_64"]

  # Hard ceiling is 900s and cannot be raised. 780s leaves headroom for the
  # adapter and response flush; generations expected to exceed it are handed
  # to ECS RunTask instead.
  timeout     = var.timeout_seconds
  memory_size = var.memory_mb

  ephemeral_storage {
    size = var.tmp_storage_mb # /tmp — app.py writes uploads and reports here
  }

  environment {
    variables = {
      FLASK_ENV             = "production"
      S3_INPUT_BUCKET       = var.s3_input_bucket
      S3_OUTPUT_BUCKET      = var.s3_output_bucket
      DYNAMODB_TABLE_NAME   = var.dynamodb_table_name
      AUTO_SAVE_TO_DYNAMODB = "true"

      # Cognito settings are NOT passed here — that would create a Terraform
      # cycle (function -> function_url -> cognito callback -> function).
      # cognito_auth.py reads them from this SSM path at cold start instead.
      COGNITO_SSM_PREFIX = var.cognito_ssm_prefix

      # Every generation is dispatched to a one-shot Fargate task rather than
      # run inline. There is deliberately no size threshold: a single code path
      # is simpler than two, and it removes the 900s ceiling from the picture
      # entirely. At current volumes the task costs a few cents per report.
      ECS_CLUSTER         = var.ecs_cluster_arn
      ECS_TASK_DEFINITION = var.ecs_task_definition_family
      ECS_CONTAINER_NAME  = var.ecs_container_name
      ECS_SUBNET_IDS      = join(",", var.ecs_subnet_ids)
      ECS_SECURITY_GROUP  = var.ecs_security_group_id

      # AWS_REGION is a reserved Lambda variable and is provided by the runtime.
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.app.name
  }

  lifecycle {
    # CodeBuild republishes :latest and calls UpdateFunctionCode, mirroring the
    # ignore_changes on the ECS service's task_definition.
    ignore_changes = [image_uri]
  }

  tags = { Name = "${var.client_name}-business-case-app" }

  depends_on = [aws_cloudwatch_log_group.app]
}

# ---------------------------------------------------------------------------
# Function URL — replaces the ALB.
#
# NOT public: AuthType is AWS_IAM, so every request must be SigV4-signed.
# CloudFront signs on the viewer's behalf via Origin Access Control, and is the
# only thing allowed to invoke this (see modules/cloudfront).
#
# An anonymous function URL was tried first and returned 403 from the Lambda
# service despite a correct resource policy — the account is a member of an AWS
# Organization that blocks anonymous invocation. Signing through CloudFront
# sidesteps that, and is the better posture anyway: it puts the app behind
# something that can carry WAF, access logs and a real domain.
#
# RESPONSE_STREAM lets responses stream rather than buffer.
# ---------------------------------------------------------------------------
resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "RESPONSE_STREAM"

  # No cors block: the browser only ever talks to CloudFront, which serves the
  # frontend and the API from one origin.
}
