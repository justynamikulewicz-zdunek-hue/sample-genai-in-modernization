# ---------------------------------------------------------------------------
# Cognito User Pool — admin-create only, no self-registration
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool" "main" {
  name = "${var.client_name}-business-case-users"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  mfa_configuration = "OFF"

  tags = { Name = "${var.client_name}-business-case-users" }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "${var.client_name}-bc-${var.cognito_domain_suffix}"
  user_pool_id = aws_cognito_user_pool.main.id
}

# ---------------------------------------------------------------------------
# App Client — OAuth code flow, Lambda Function URL callback
# Replaces CF Lambda GetClientSecretLambda: client_secret is a direct attribute
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.client_name}-business-case-app"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  callback_urls = ["${var.app_url}/auth/callback"]
  logout_urls   = ["${var.app_url}/logout"]

  supported_identity_providers = ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
}

# ---------------------------------------------------------------------------
# Cognito config in SSM — read by the app at cold start (cognito_auth.py).
#
# These cannot be passed to the Lambda as environment variables: the app's
# Function URL is what callback_urls above point at, so the function would have
# to exist before Cognito, and Cognito before the function. Routing the values
# through SSM breaks that cycle. It also keeps the client secret out of the
# function's environment, where GetFunctionConfiguration would expose it.
# ---------------------------------------------------------------------------
resource "aws_ssm_parameter" "user_pool_id" {
  name  = "${var.ssm_prefix}/user_pool_id"
  type  = "String"
  value = aws_cognito_user_pool.main.id
  tags  = { Client = var.client_name }
}

resource "aws_ssm_parameter" "client_id" {
  name  = "${var.ssm_prefix}/client_id"
  type  = "String"
  value = aws_cognito_user_pool_client.app.id
  tags  = { Client = var.client_name }
}

resource "aws_ssm_parameter" "client_secret" {
  name  = "${var.ssm_prefix}/client_secret"
  type  = "SecureString"
  value = aws_cognito_user_pool_client.app.client_secret
  tags  = { Client = var.client_name }
}

resource "aws_ssm_parameter" "domain" {
  name  = "${var.ssm_prefix}/domain"
  type  = "String"
  value = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com"
  tags  = { Client = var.client_name }
}

resource "aws_ssm_parameter" "app_url" {
  name  = "${var.ssm_prefix}/app_url"
  type  = "String"
  value = var.app_url
  tags  = { Client = var.client_name }
}

# ---------------------------------------------------------------------------
# Initial admin user — Cognito sends temporary password by email
# ---------------------------------------------------------------------------
resource "aws_cognito_user" "admin" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = var.admin_email

  attributes = {
    email          = var.admin_email
    email_verified = "true"
  }
}
