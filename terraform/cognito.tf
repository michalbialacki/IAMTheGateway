locals {
  user_pool_name = "${local.name_prefix}-user-pool"

  # 5 departments × 5 clearance levels = 25 groups.
  # Format: dept_{department}_cl_{level_number}
  # Lambda Authorizer parses: ^dept_([a-z]+)_cl_(\d+)$
  cognito_groups = toset(flatten([
    for dept in var.cognito_departments : [
      for level_name, level_num in var.clearance_levels :
      "dept_${dept}_cl_${level_num}"
    ]
  ]))
}

# ─── User Pool ────────────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "main" {
  name = local.user_pool_name

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Only admins can create users – no self-registration
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# ─── App Client (public – CLI and future Android) ─────────────────────────────

resource "aws_cognito_user_pool_client" "cli" {
  name         = "${local.name_prefix}-cli-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  access_token_validity  = 1 # hours – short window; revocation table covers stolen tokens
  id_token_validity      = 1 # hours
  refresh_token_validity = 7 # days

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

# ─── ABAC Groups ──────────────────────────────────────────────────────────────

resource "aws_cognito_user_group" "abac" {
  for_each     = local.cognito_groups
  name         = each.key
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "ABAC group: ${each.key}"
}
