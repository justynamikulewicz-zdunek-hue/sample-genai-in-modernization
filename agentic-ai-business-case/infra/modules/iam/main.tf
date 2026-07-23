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
