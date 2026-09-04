terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

# ---------------------------------------------------------------------------
# Terraform remote state — S3 bucket
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket        = var.state_bucket_name
  force_destroy = false

  tags = { Purpose = "terraform-state", Project = "map-accelerator" }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# Terraform remote state — DynamoDB lock table
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "tflock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = { Purpose = "terraform-state-lock", Project = "map-accelerator" }
}

# ---------------------------------------------------------------------------
# GitHub OIDC provider (for GitHub Actions CI/CD)
# ---------------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]
}

# ---------------------------------------------------------------------------
# terraform-deployer IAM role
# Trust: SSO AdministratorAccess (local dev) + GitHub Actions (CI/CD)
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "terraform_deployer_trust" {
  # Local deploy via SSO AdministratorAccess
  # Using account root principal — any entity with sts:AssumeRole permission can assume this role
  # In practice only SSO AdministratorAccess users have that permission in this account
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.account_id}:root"]
    }
  }

  # GitHub Actions OIDC
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "terraform_deployer" {
  name                 = "terraform-deployer"
  assume_role_policy   = data.aws_iam_policy_document.terraform_deployer_trust.json
  max_session_duration = 3600

  tags = { Purpose = "terraform-ci-cd", Project = "map-accelerator" }
}

data "aws_iam_policy_document" "terraform_deployer_permissions" {
  statement {
    sid    = "StateAccess"
    effect = "Allow"
    actions = [
      "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket",
      "s3:GetBucketVersioning", "s3:GetBucketLocation"
    ]
    resources = [
      aws_s3_bucket.tfstate.arn,
      "${aws_s3_bucket.tfstate.arn}/*"
    ]
  }

  statement {
    sid       = "StateLock"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:DescribeTable"]
    resources = [aws_dynamodb_table.tflock.arn]
  }

  statement {
    sid       = "EC2Full"
    effect    = "Allow"
    actions   = ["ec2:*"]
    resources = ["*"]
  }

  statement {
    sid       = "ECSFull"
    effect    = "Allow"
    actions   = ["ecs:*"]
    resources = ["*"]
  }

  statement {
    sid       = "ECRFull"
    effect    = "Allow"
    actions   = ["ecr:*"]
    resources = ["*"]
  }

  statement {
    sid    = "IAMLimited"
    effect = "Allow"
    actions = [
      "iam:CreateRole", "iam:DeleteRole",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy",
      "iam:GetRole", "iam:GetRolePolicy",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole", "iam:ListRoleTags",
      "iam:PassRole", "iam:TagRole", "iam:UntagRole",
      "iam:CreatePolicy", "iam:DeletePolicy",
      "iam:GetPolicy", "iam:GetPolicyVersion",
      "iam:ListPolicyVersions", "iam:TagPolicy",
      # Updating an existing managed policy creates a new version of it, so
      # CreatePolicy alone is not enough to change one after it exists.
      "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
      "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider", "iam:TagOpenIDConnectProvider"
    ]
    resources = ["*"]
  }

  statement {
    sid       = "S3Full"
    effect    = "Allow"
    actions   = ["s3:*"]
    resources = ["*"]
  }

  statement {
    sid       = "DynamoDBFull"
    effect    = "Allow"
    actions   = ["dynamodb:*"]
    resources = ["*"]
  }

  statement {
    sid       = "CognitoFull"
    effect    = "Allow"
    actions   = ["cognito-idp:*"]
    resources = ["*"]
  }

  # The web tier moved from ECS Fargate behind an ALB to a Lambda with a
  # Function URL, so the deployer needs to manage functions, their URLs and
  # their resource policies.
  statement {
    sid       = "LambdaFull"
    effect    = "Allow"
    actions   = ["lambda:*"]
    resources = ["*"]
  }

  # CloudFront fronts the Lambda Function URL. The function URL itself is not
  # public — organisation policy blocks anonymous invocation — so CloudFront
  # signs requests to it via Origin Access Control.
  statement {
    sid    = "CloudFrontFull"
    effect = "Allow"
    actions = [
      "cloudfront:*",
      # OAC signing needs the distribution to assume a service-linked role.
      "iam:CreateServiceLinkedRole"
    ]
    resources = ["*"]
  }

  # Kept for clients who still mandate an ALB; the default stack no longer
  # creates one.
  statement {
    sid       = "ELBFull"
    effect    = "Allow"
    actions   = ["elasticloadbalancing:*"]
    resources = ["*"]
  }

  statement {
    sid       = "LogsFull"
    effect    = "Allow"
    actions   = ["logs:*"]
    resources = ["*"]
  }

  statement {
    sid       = "CodeBuildFull"
    effect    = "Allow"
    actions   = ["codebuild:*"]
    resources = ["*"]
  }

  statement {
    sid       = "ACMFull"
    effect    = "Allow"
    actions   = ["acm:*"]
    resources = ["*"]
  }

  statement {
    sid       = "SSMFull"
    effect    = "Allow"
    actions   = ["ssm:*"]
    resources = ["*"]
  }

  statement {
    sid    = "BedrockRead"
    effect = "Allow"
    actions = [
      "bedrock:ListFoundationModels",
      "bedrock:GetFoundationModel"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "terraform_deployer" {
  name   = "terraform-deployer-policy"
  policy = data.aws_iam_policy_document.terraform_deployer_permissions.json
}

resource "aws_iam_role_policy_attachment" "terraform_deployer" {
  role       = aws_iam_role.terraform_deployer.name
  policy_arn = aws_iam_policy.terraform_deployer.arn
}
