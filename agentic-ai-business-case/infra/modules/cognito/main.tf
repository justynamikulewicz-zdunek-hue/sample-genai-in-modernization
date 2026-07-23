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
# App Client — OAuth code flow, ALB callback
# Replaces CF Lambda GetClientSecretLambda: client_secret is a direct attribute
# ---------------------------------------------------------------------------
resource "aws_cognito_user_pool_client" "app" {
  name         = "${var.client_name}-business-case-app"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  callback_urls = ["https://${var.alb_dns_name}/auth/callback"]
  logout_urls   = ["https://${var.alb_dns_name}/logout"]

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
