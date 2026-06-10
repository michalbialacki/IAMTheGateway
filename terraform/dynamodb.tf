# ─── sessions ─────────────────────────────────────────────────────────────────
# Przechowuje aktywne sesje: user_id, department, clearance_level, jti, TTL.
# GSI na user_id umożliwia listowanie sesji per użytkownik.

resource "aws_dynamodb_table" "sessions" {
  name         = "${local.name_prefix}-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  global_secondary_index {
    name            = "user_id-index"
    hash_key        = "user_id"
    projection_type = "ALL"
  }

  server_side_encryption {
    enabled = true
  }
}

# ─── conversation_history ─────────────────────────────────────────────────────
# Hash: session_id, Sort: turn_index (Number).
# Query: session_id DESC LIMIT 5 → ostatnie 3-5 wymian do kontekstu promptu.

resource "aws_dynamodb_table" "conversation_history" {
  name         = "${local.name_prefix}-conversation-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"
  range_key    = "turn_index"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "turn_index"
    type = "N"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}

# ─── revoked_tokens ───────────────────────────────────────────────────────────
# Hash: jti (JWT ID claim). Lambda Authorizer sprawdza przy każdym requeście.
# TTL na expires_at = expiry tokenu → automatyczne czyszczenie bez backlogu.

resource "aws_dynamodb_table" "revoked_tokens" {
  name         = "${local.name_prefix}-revoked-tokens"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "jti"

  attribute {
    name = "jti"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }
}
