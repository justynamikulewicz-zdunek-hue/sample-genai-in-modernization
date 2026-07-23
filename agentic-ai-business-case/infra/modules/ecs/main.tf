resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.client_name}-business-case-generator"
  retention_in_days = 7

  tags = { Name = "${var.client_name}-ecs-logs" }
}

# ---------------------------------------------------------------------------
# ECS Cluster
# ---------------------------------------------------------------------------
resource "aws_ecs_cluster" "main" {
  name = "${var.client_name}-ecs-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Name = "${var.client_name}-ecs-cluster" }
}

# ---------------------------------------------------------------------------
# SSM SecureString — Cognito client secret
# Stored in SSM to avoid plain-text in task definition API response
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "cognito_client_secret" {
  name  = "/${var.client_name}/cognito/client_secret"
  type  = "SecureString"
  value = var.cognito_client_secret

  tags = { Client = var.client_name }
}

# ---------------------------------------------------------------------------
# ECS Task Definition
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

    portMappings = [{
      containerPort = 8080
      hostPort      = 8080
      protocol      = "tcp"
    }]

    environment = [
      { name = "FLASK_ENV",             value = "production" },
      { name = "AWS_REGION",            value = var.aws_region },
      { name = "S3_INPUT_BUCKET",       value = var.s3_input_bucket },
      { name = "S3_OUTPUT_BUCKET",      value = var.s3_output_bucket },
      { name = "DYNAMODB_TABLE_NAME",   value = var.dynamodb_table_name },
      { name = "AUTO_SAVE_TO_DYNAMODB", value = "true" },
      { name = "COGNITO_USER_POOL_ID",  value = var.cognito_user_pool_id },
      { name = "COGNITO_CLIENT_ID",     value = var.cognito_client_id },
      { name = "COGNITO_DOMAIN",        value = var.cognito_domain },
      { name = "APP_URL",               value = var.app_url }
    ]

    # Cognito client secret pulled from SSM at container start (never in logs)
    secrets = [{
      name      = "COGNITO_CLIENT_SECRET"
      valueFrom = aws_ssm_parameter.cognito_client_secret.arn
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "ecs"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')\" || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 60
    }

    essential = true
  }])

  tags = { Name = "${var.client_name}-business-case-task" }
}

# ---------------------------------------------------------------------------
# ECS Service
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "app" {
  name            = "${var.client_name}-business-case-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Grace period gives time for the container to start before ALB marks it unhealthy
  health_check_grace_period_seconds = 120

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "${var.client_name}-business-case-generator"
    container_port   = 8080
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = { Name = "${var.client_name}-business-case-service" }

  lifecycle {
    # Allow CodeBuild/CI to update task_definition without Terraform reverting it
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_ecs_task_definition.app]
}
