resource "aws_dynamodb_table" "cases" {
  name         = "${var.client_name}-business-case-cases"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "caseId"
  range_key    = "createdAt"

  attribute {
    name = "caseId"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "S"
  }

  attribute {
    name = "userId"
    type = "S"
  }

  # GSI for per-user case history
  global_secondary_index {
    name            = "UserIdIndex"
    hash_key        = "userId"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  tags = { Name = "${var.client_name}-business-case-cases" }
}
