#!/usr/bin/env bash
# Bootstrap: GitHub OIDC provider, terraform-deployer IAM role, S3 state bucket, DynamoDB lock table
# Run once per AWS account with AdministratorAccess credentials.
# Usage: AWS_PROFILE=stxnext-devops bash setup-iam-and-state.sh

set -euo pipefail

ACCOUNT_ID="680696743786"
REGION="eu-north-1"
GITHUB_ORG="justynamikulewicz-zdunek-hue"
GITHUB_REPO="sample-genai-in-modernization"
ROLE_NAME="terraform-deployer"
POLICY_NAME="terraform-deployer-policy"
STATE_BUCKET="map-accelerator-tfstate-${ACCOUNT_ID}"
LOCK_TABLE="terraform-state-lock"
SSO_ROLE_PATTERN="arn:aws:iam::${ACCOUNT_ID}:role/aws-reserved/sso.amazonaws.com/*"

export AWS_PAGER=""

echo "==> [1/5] Creating GitHub Actions OIDC Provider..."
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" &>/dev/null; then
  echo "     OIDC provider already exists, skipping."
else
  aws iam create-open-id-connect-provider \
    --url "https://token.actions.githubusercontent.com" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  echo "     Created."
fi

echo "==> [2/5] Creating trust policy for terraform-deployer..."
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSSOAdminAssumeRole",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::${ACCOUNT_ID}:root"
      },
      "Action": "sts:AssumeRole",
      "Condition": {
        "ArnLike": {
          "aws:PrincipalArn": "${SSO_ROLE_PATTERN}"
        }
      }
    },
    {
      "Sid": "AllowGitHubActionsOIDC",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${GITHUB_REPO}:*"
        }
      }
    }
  ]
}
EOF

echo "==> [3/5] Creating IAM Role terraform-deployer..."
if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
  echo "     Role already exists, updating trust policy..."
  aws iam update-assume-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-document file:///tmp/trust-policy.json
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file:///tmp/trust-policy.json \
    --description "Terraform deployer for MAP Agentic Accelerator (GenAI PoC)" \
    --tags Key=Project,Value=map-agentic-accelerator Key=ManagedBy,Value=bootstrap
fi

echo "==> [4/5] Creating and attaching permissions policy..."
cat > /tmp/terraform-deployer-policy.json <<'POLICY'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2Networking",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:DescribeVpcs", "ec2:ModifyVpcAttribute",
        "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:DescribeSubnets", "ec2:ModifySubnetAttribute",
        "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
        "ec2:AttachInternetGateway", "ec2:DetachInternetGateway", "ec2:DescribeInternetGateways",
        "ec2:AllocateAddress", "ec2:ReleaseAddress", "ec2:DescribeAddresses", "ec2:DescribeAddressesAttribute",
        "ec2:CreateNatGateway", "ec2:DeleteNatGateway", "ec2:DescribeNatGateways",
        "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:DescribeRouteTables",
        "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:ReplaceRoute",
        "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
        "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup", "ec2:DescribeSecurityGroups",
        "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress",
        "ec2:UpdateSecurityGroupRuleDescriptionsIngress", "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
        "ec2:DescribeSecurityGroupRules", "ec2:ModifySecurityGroupRules",
        "ec2:CreateTags", "ec2:DeleteTags", "ec2:DescribeTags",
        "ec2:DescribeAvailabilityZones", "ec2:DescribeRegions",
        "ec2:DescribeAccountAttributes", "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVpcAttribute", "ec2:DescribeNetworkAcls"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECS",
      "Effect": "Allow",
      "Action": [
        "ecs:CreateCluster", "ecs:DeleteCluster", "ecs:DescribeClusters",
        "ecs:UpdateCluster", "ecs:UpdateClusterSettings", "ecs:PutClusterCapacityProviders",
        "ecs:RegisterTaskDefinition", "ecs:DeregisterTaskDefinition", "ecs:DescribeTaskDefinition",
        "ecs:ListTaskDefinitions", "ecs:ListTaskDefinitionFamilies",
        "ecs:CreateService", "ecs:DeleteService", "ecs:UpdateService", "ecs:DescribeServices",
        "ecs:ListServices", "ecs:ListClusters",
        "ecs:TagResource", "ecs:UntagResource", "ecs:ListTagsForResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECR",
      "Effect": "Allow",
      "Action": [
        "ecr:CreateRepository", "ecr:DeleteRepository", "ecr:DescribeRepositories",
        "ecr:PutLifecyclePolicy", "ecr:GetLifecyclePolicy", "ecr:DeleteLifecyclePolicy",
        "ecr:SetRepositoryPolicy", "ecr:GetRepositoryPolicy", "ecr:DeleteRepositoryPolicy",
        "ecr:TagResource", "ecr:UntagResource", "ecr:ListTagsForResource",
        "ecr:PutImageScanningConfiguration", "ecr:PutImageTagMutability",
        "ecr:DescribeImages", "ecr:ListImages",
        "ecr:GetAuthorizationToken",
        "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
        "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        "ecr:BatchDeleteImage"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ALB",
      "Effect": "Allow",
      "Action": [
        "elasticloadbalancing:CreateLoadBalancer", "elasticloadbalancing:DeleteLoadBalancer",
        "elasticloadbalancing:DescribeLoadBalancers", "elasticloadbalancing:ModifyLoadBalancerAttributes",
        "elasticloadbalancing:DescribeLoadBalancerAttributes",
        "elasticloadbalancing:CreateTargetGroup", "elasticloadbalancing:DeleteTargetGroup",
        "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:ModifyTargetGroup",
        "elasticloadbalancing:ModifyTargetGroupAttributes", "elasticloadbalancing:DescribeTargetGroupAttributes",
        "elasticloadbalancing:CreateListener", "elasticloadbalancing:DeleteListener",
        "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:ModifyListener",
        "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags", "elasticloadbalancing:DescribeTags",
        "elasticloadbalancing:SetSecurityGroups", "elasticloadbalancing:SetSubnets",
        "elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets",
        "elasticloadbalancing:DescribeTargetHealth",
        "elasticloadbalancing:CreateRule", "elasticloadbalancing:DeleteRule",
        "elasticloadbalancing:DescribeRules", "elasticloadbalancing:ModifyRule",
        "elasticloadbalancing:DescribeListenerCertificates",
        "elasticloadbalancing:AddListenerCertificates", "elasticloadbalancing:RemoveListenerCertificates"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket", "s3:DeleteBucket", "s3:ListBucket", "s3:GetBucketLocation",
        "s3:PutBucketVersioning", "s3:GetBucketVersioning",
        "s3:PutBucketEncryption", "s3:GetBucketEncryption",
        "s3:PutBucketTagging", "s3:GetBucketTagging", "s3:DeleteBucketTagging",
        "s3:PutBucketPublicAccessBlock", "s3:GetBucketPublicAccessBlock",
        "s3:PutBucketPolicy", "s3:GetBucketPolicy", "s3:DeleteBucketPolicy",
        "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
        "s3:GetBucketAcl", "s3:PutBucketAcl",
        "s3:GetBucketCORS", "s3:PutBucketCORS", "s3:DeleteBucketCORS",
        "s3:GetBucketWebsite", "s3:DeleteBucketWebsite",
        "s3:GetBucketObjectLockConfiguration", "s3:PutBucketObjectLockConfiguration",
        "s3:GetAccelerateConfiguration", "s3:PutAccelerateConfiguration",
        "s3:ListAllMyBuckets", "s3:ListBucketVersions",
        "s3:GetBucketLogging", "s3:PutBucketLogging",
        "s3:GetBucketNotification", "s3:PutBucketNotification",
        "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:DescribeTable", "dynamodb:UpdateTable",
        "dynamodb:TagResource", "dynamodb:UntagResource", "dynamodb:ListTagsOfResource",
        "dynamodb:DescribeContinuousBackups", "dynamodb:UpdateContinuousBackups",
        "dynamodb:DescribeTimeToLive", "dynamodb:UpdateTimeToLive",
        "dynamodb:ListTables", "dynamodb:DescribeGlobalTable",
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem",
        "dynamodb:DescribeTableReplicaAutoScaling",
        "dynamodb:DescribeKinesisStreamingDestination"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Cognito",
      "Effect": "Allow",
      "Action": [
        "cognito-idp:CreateUserPool", "cognito-idp:DeleteUserPool",
        "cognito-idp:DescribeUserPool", "cognito-idp:UpdateUserPool",
        "cognito-idp:CreateUserPoolClient", "cognito-idp:DeleteUserPoolClient",
        "cognito-idp:DescribeUserPoolClient", "cognito-idp:UpdateUserPoolClient",
        "cognito-idp:ListUserPoolClients",
        "cognito-idp:CreateUserPoolDomain", "cognito-idp:DeleteUserPoolDomain",
        "cognito-idp:DescribeUserPoolDomain",
        "cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser",
        "cognito-idp:AdminGetUser", "cognito-idp:ListUsers",
        "cognito-idp:TagResource", "cognito-idp:UntagResource", "cognito-idp:ListTagsForResource",
        "cognito-idp:ListUserPools",
        "cognito-idp:SetUserPoolMfaConfig", "cognito-idp:GetUserPoolMfaConfig"
      ],
      "Resource": "*"
    },
    {
      "Sid": "IAMScopedToProject",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole",
        "iam:UpdateRoleDescription", "iam:UpdateAssumeRolePolicy",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
        "iam:PutRolePolicy", "iam:DeleteRolePolicy",
        "iam:GetRolePolicy", "iam:ListRolePolicies",
        "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy",
        "iam:CreatePolicyVersion", "iam:DeletePolicyVersion",
        "iam:GetPolicyVersion", "iam:ListPolicyVersions", "iam:SetDefaultPolicyVersion",
        "iam:ListPolicies", "iam:ListRoles",
        "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags",
        "iam:TagPolicy", "iam:UntagPolicy", "iam:ListPolicyTags",
        "iam:GetOpenIDConnectProvider", "iam:CreateOpenIDConnectProvider",
        "iam:DeleteOpenIDConnectProvider", "iam:ListOpenIDConnectProviders",
        "iam:AddClientIDToOpenIDConnectProvider"
      ],
      "Resource": "*"
    },
    {
      "Sid": "IAMPassRoleScopedToECS",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": [
            "ecs-tasks.amazonaws.com",
            "codebuild.amazonaws.com"
          ]
        }
      }
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:DescribeLogGroups",
        "logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy",
        "logs:TagLogGroup", "logs:UntagLogGroup",
        "logs:ListTagsLogGroup", "logs:ListTagsForResource",
        "logs:TagResource", "logs:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "CodeBuild",
      "Effect": "Allow",
      "Action": [
        "codebuild:CreateProject", "codebuild:DeleteProject",
        "codebuild:UpdateProject", "codebuild:BatchGetProjects",
        "codebuild:ListProjects",
        "codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:StopBuild",
        "codebuild:CreateWebhook", "codebuild:DeleteWebhook", "codebuild:UpdateWebhook",
        "codebuild:ListBuildsForProject",
        "codebuild:TagResource", "codebuild:UntagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ACM",
      "Effect": "Allow",
      "Action": [
        "acm:ImportCertificate", "acm:DeleteCertificate",
        "acm:DescribeCertificate", "acm:ListCertificates",
        "acm:RequestCertificate", "acm:GetCertificate",
        "acm:AddTagsToCertificate", "acm:RemoveTagsFromCertificate",
        "acm:ListTagsForCertificate"
      ],
      "Resource": "*"
    },
    {
      "Sid": "STS",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "sts:AssumeRole"
      ],
      "Resource": "*"
    }
  ]
}
POLICY

POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"
if aws iam get-policy --policy-arn "$POLICY_ARN" &>/dev/null; then
  echo "     Policy exists, creating new version..."
  # Delete oldest version if at limit (max 5)
  VERSIONS=$(aws iam list-policy-versions --policy-arn "$POLICY_ARN" \
    --query 'Versions[?!IsDefaultVersion].VersionId' --output text)
  for v in $VERSIONS; do
    aws iam delete-policy-version --policy-arn "$POLICY_ARN" --version-id "$v" || true
  done
  aws iam create-policy-version \
    --policy-arn "$POLICY_ARN" \
    --policy-document file:///tmp/terraform-deployer-policy.json \
    --set-as-default
else
  aws iam create-policy \
    --policy-name "$POLICY_NAME" \
    --policy-document file:///tmp/terraform-deployer-policy.json \
    --description "Granular permissions for terraform-deployer role (MAP Agentic Accelerator)" \
    --tags Key=Project,Value=map-agentic-accelerator Key=ManagedBy,Value=bootstrap
fi

aws iam attach-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-arn "$POLICY_ARN" 2>/dev/null || echo "     Policy already attached."

echo "==> [5/5] Creating S3 state bucket and DynamoDB lock table..."

if aws s3api head-bucket --bucket "$STATE_BUCKET" --region "$REGION" 2>/dev/null; then
  echo "     S3 bucket already exists."
else
  aws s3api create-bucket \
    --bucket "$STATE_BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  aws s3api put-bucket-versioning \
    --bucket "$STATE_BUCKET" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption \
    --bucket "$STATE_BUCKET" \
    --server-side-encryption-configuration '{
      "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
    }'
  aws s3api put-public-access-block \
    --bucket "$STATE_BUCKET" \
    --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  echo "     S3 bucket created: $STATE_BUCKET"
fi

if aws dynamodb describe-table --table-name "$LOCK_TABLE" --region "$REGION" &>/dev/null; then
  echo "     DynamoDB table already exists."
else
  aws dynamodb create-table \
    --table-name "$LOCK_TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION" \
    --tags Key=Project,Value=map-agentic-accelerator Key=ManagedBy,Value=bootstrap
  echo "     DynamoDB table created: $LOCK_TABLE"
fi

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

echo ""
echo "======================================================"
echo " Bootstrap complete!"
echo "======================================================"
echo " Role ARN : ${ROLE_ARN}"
echo " S3 Bucket: ${STATE_BUCKET}"
echo " DynamoDB : ${LOCK_TABLE} (${REGION})"
echo ""
echo " Add to ~/.aws/config:"
echo ""
echo " [profile terraform-deployer]"
echo " role_arn = ${ROLE_ARN}"
echo " source_profile = stxnext-devops"
echo " region = ${REGION}"
echo " output = json"
echo "======================================================"

rm -f /tmp/trust-policy.json /tmp/terraform-deployer-policy.json
