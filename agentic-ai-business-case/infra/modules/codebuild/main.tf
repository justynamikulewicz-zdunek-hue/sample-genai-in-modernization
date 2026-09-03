resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/codebuild/${var.client_name}-business-case-build"
  retention_in_days = 7

  tags = { Name = "${var.client_name}-codebuild-logs" }
}

resource "aws_codebuild_project" "app" {
  name          = "${var.client_name}-business-case-build"
  description   = "Builds Docker image for ${var.client_name} and pushes to ECR"
  build_timeout = 30
  service_role  = var.codebuild_role_arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = true # required for Docker

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = var.account_id
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }

    environment_variable {
      name  = "ECR_REPO_URL"
      value = var.ecr_repository_url
    }

    environment_variable {
      name  = "PROJECT_SUBDIR"
      value = var.project_subdir
    }

    environment_variable {
      name  = "LAMBDA_FUNCTION_NAME"
      value = var.lambda_function_name
    }
  }

  source {
    type            = "GITHUB"
    location        = var.github_repo_url
    git_clone_depth = 1
    buildspec       = <<-BUILDSPEC
      version: 0.2
      phases:
        pre_build:
          commands:
            - echo Logging in to Amazon ECR...
            - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
            - COMMIT_HASH=$(echo $CODEBUILD_RESOLVED_SOURCE_VERSION | cut -c 1-7)
            - IMAGE_TAG=$${COMMIT_HASH:-latest}
        build:
          commands:
            - echo Build started on $(date)
            - cd $PROJECT_SUBDIR
            - docker build -f infrastructure/Dockerfile -t $ECR_REPO_URL:latest -t $ECR_REPO_URL:$IMAGE_TAG .
        post_build:
          commands:
            - echo Pushing image to ECR...
            - docker push $ECR_REPO_URL:latest
            - docker push $ECR_REPO_URL:$IMAGE_TAG
            # A Lambda pins the image digest at deploy time, so pushing to ECR
            # does not roll the function forward on its own. Point it at the
            # immutable tag rather than :latest so the deployed version is
            # traceable back to a commit.
            - echo Rolling image onto Lambda $LAMBDA_FUNCTION_NAME...
            - aws lambda update-function-code --function-name $LAMBDA_FUNCTION_NAME --image-uri $ECR_REPO_URL:$IMAGE_TAG --region $AWS_DEFAULT_REGION --no-cli-pager
            - aws lambda wait function-updated --function-name $LAMBDA_FUNCTION_NAME --region $AWS_DEFAULT_REGION
            - echo Build complete. Image $ECR_REPO_URL:$IMAGE_TAG is live.
    BUILDSPEC
  }

  source_version = var.github_branch

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.codebuild.name
      stream_name = "build"
    }
  }

  tags = { Name = "${var.client_name}-business-case-build" }
}
