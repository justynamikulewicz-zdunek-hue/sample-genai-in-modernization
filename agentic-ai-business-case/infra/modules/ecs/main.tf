resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.client_name}-business-case-generator"
  retention_in_days = 7

  tags = { Name = "${var.client_name}-ecs-logs" }
}

# ---------------------------------------------------------------------------
# ECS Cluster
#
# No long-running service any more. This cluster exists purely as a place to
# run one-shot generation jobs (ecs:RunTask) that would exceed Lambda's
# non-negotiable 900s ceiling. Idle cost is zero: with no task running, a
# Fargate cluster bills nothing.
#
# Container Insights is off. It bills per ingested metric and was charging for
# detailed telemetry on a container that mostly sat idle; for tasks that live a
# few minutes the task logs are enough.
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${var.client_name}-ecs-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }

  tags = { Name = "${var.client_name}-ecs-cluster" }
}

# ---------------------------------------------------------------------------
# Task Definition — the generation job
#
# Same image as the Lambda web tier; the Lambda Web Adapter baked into it is
# inert outside Lambda. RunTask overrides the command to invoke the generator
# script directly, so gunicorn never starts here.
#
# Carries no Cognito configuration: the job authenticates nobody, it just reads
# input from S3, calls Bedrock and writes reports back.
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "app" {
  family                   = "${var.client_name}-business-case-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = var.task_execution_role_arn
  task_role_arn            = var.task_role_arn

  container_definitions = jsonencode([{
    name  = "${var.client_name}-business-case-generator"
    image = "${var.ecr_repository_url}:latest"

    environment = [
      { name = "FLASK_ENV", value = "production" },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "S3_INPUT_BUCKET", value = var.s3_input_bucket },
      { name = "S3_OUTPUT_BUCKET", value = var.s3_output_bucket },
      { name = "DYNAMODB_TABLE_NAME", value = var.dynamodb_table_name },
      { name = "AUTO_SAVE_TO_DYNAMODB", value = "true" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "job"
      }
    }

    essential = true
  }])

  tags = { Name = "${var.client_name}-business-case-task" }
}
