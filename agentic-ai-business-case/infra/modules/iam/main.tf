# ---------------------------------------------------------------------------
# ECS Task Execution Role
# Used by AWS ECS agent: pull image from ECR, write logs to CloudWatch
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_task_execution_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.client_name}-ecs-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_execution_assume.json

  tags = { Name = "${var.client_name}-ecs-task-execution-role" }
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# SSM read access for Cognito client secret (needed by execution role for container secrets)
resource "aws_iam_role_policy" "ecs_task_execution_ssm" {
  name = "${var.client_name}-ecs-exec-ssm"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters", "ssm:GetParameter"]
      Resource = "arn:aws:ssm:*:*:parameter/${var.client_name}/*"
    }]
  })
}

# ---------------------------------------------------------------------------
# ECS Task Role
# Used by application code: Bedrock, S3, DynamoDB, Pricing API
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task" {
  name               = "${var.client_name}-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json

  tags = { Name = "${var.client_name}-ecs-task-role" }
}

data "aws_iam_policy_document" "ecs_task_permissions" {
  # Bedrock — invoke Claude 3 Sonnet across all 9 agents
  statement {
    sid    = "BedrockInvoke"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream"
    ]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:*:inference-profile/*"
    ]
  }

  # S3 — input/output buckets (RVTools uploads, generated reports)
  statement {
    sid    = "S3Objects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject"
    ]
    resources = [
      "${var.s3_input_bucket_arn}/*",
      "${var.s3_output_bucket_arn}/*"
    ]
  }

  statement {
    sid       = "S3List"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.s3_input_bucket_arn, var.s3_output_bucket_arn]
  }

  # DynamoDB — agent session state and case history
  statement {
    sid    = "DynamoDBCRUD"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan"
    ]
    resources = [
      var.dynamodb_table_arn,
      "${var.dynamodb_table_arn}/index/*"
    ]
  }

  # AWS Pricing API — migration cost calculations
  statement {
    sid    = "PricingAPI"
    effect = "Allow"
    actions = [
      "pricing:GetProducts",
      "pricing:DescribeServices",
      "pricing:GetAttributeValues"
    ]
    resources = ["*"]
  }

  # Savings Plans — migration financial modelling
  statement {
    sid    = "SavingsPlans"
    effect = "Allow"
    actions = [
      "savingsplans:DescribeSavingsPlansOfferingRates",
      "savingsplans:DescribeSavingsPlansOfferings"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "ecs_task" {
  name   = "${var.client_name}-ecs-task-policy"
  policy = data.aws_iam_policy_document.ecs_task_permissions.json

  tags = { Name = "${var.client_name}-ecs-task-policy" }
}

resource "aws_iam_role_policy_attachment" "ecs_task" {
  role       = aws_iam_role.ecs_task.name
  policy_arn = aws_iam_policy.ecs_task.arn
}

# ---------------------------------------------------------------------------
# Lambda Execution Role — the web tier
#
# Runs the same application code as the ECS task, so it reuses the same
# permission policy rather than restating Bedrock/S3/DynamoDB/Pricing grants.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.client_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = { Name = "${var.client_name}-lambda-role" }
}

# CloudWatch Logs
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Same application permissions the ECS task role has
resource "aws_iam_role_policy_attachment" "lambda_app" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.ecs_task.arn
}

data "aws_iam_policy_document" "lambda_extra" {
  # Cognito config is read from SSM at cold start — see cognito_auth.py
  statement {
    sid    = "SSMCognitoConfig"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath"
    ]
    resources = ["arn:aws:ssm:*:*:parameter/${var.client_name}/*"]
  }

  # Hand long-running generations to a one-shot Fargate task instead of
  # burning against Lambda's non-negotiable 900s ceiling.
  statement {
    sid       = "RunGenerationTask"
    effect    = "Allow"
    actions   = ["ecs:RunTask", "ecs:DescribeTasks", "ecs:StopTask"]
    resources = ["*"]
  }

  # RunTask must be able to hand the task its roles.
  statement {
    sid       = "PassTaskRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_task.arn, aws_iam_role.ecs_task_execution.arn]
  }
}

resource "aws_iam_role_policy" "lambda_extra" {
  name   = "${var.client_name}-lambda-extra"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_extra.json
}

# ---------------------------------------------------------------------------
# CodeBuild Role — build Docker image & push to ECR
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${var.client_name}-codebuild-role"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json

  tags = { Name = "${var.client_name}-codebuild-role" }
}

data "aws_iam_policy_document" "codebuild_permissions" {
  statement {
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage"
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["*"]
  }

  # Pushing :latest to ECR does not update a Lambda — the function pins the
  # image digest at deploy time. The build must tell Lambda to pick up the new
  # image, otherwise `codebuild start-build` would silently change nothing.
  # Scoped by name pattern to avoid a dependency on the lambda module.
  statement {
    sid    = "UpdateLambdaImage"
    effect = "Allow"
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:GetFunction",
      # `aws lambda wait function-updated` polls GetFunctionConfiguration,
      # which is a separate IAM action from GetFunction — without it the build
      # pushes the image successfully and then fails on the verification step.
      "lambda:GetFunctionConfiguration"
    ]
    resources = ["arn:aws:lambda:*:*:function:${var.client_name}-*"]
  }
}

resource "aws_iam_policy" "codebuild" {
  name   = "${var.client_name}-codebuild-policy"
  policy = data.aws_iam_policy_document.codebuild_permissions.json

  tags = { Name = "${var.client_name}-codebuild-policy" }
}

resource "aws_iam_role_policy_attachment" "codebuild" {
  role       = aws_iam_role.codebuild.name
  policy_arn = aws_iam_policy.codebuild.arn
}
