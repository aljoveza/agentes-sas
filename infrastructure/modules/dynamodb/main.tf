# Single-table company ledger. Key layout is owned by services/company-mcp (repository.py):
#   ORDER#<id>     META | RESERVATION | INVOICE | PAYMENT | SHIPMENT
#   CUSTOMER#<id>  PROFILE        PRODUCT#<id>  INVENTORY
#   TASK#<id>      META   gsi1: ASSIGNEE#<agent> / <status>#<createdAt>#<id>
#   DECISION#<id>  META   gsi1: DECISIONS        / <status>#<createdAt>#<id>
#   orders                gsi1: ORDERS           / <status>#<createdAt>#<id>

resource "aws_dynamodb_table" "this" {
  name                        = var.name
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  attribute {
    name = "gsi1pk"
    type = "S"
  }

  attribute {
    name = "gsi1sk"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi1"
    projection_type = "ALL"

    key_schema {
      attribute_name = "gsi1pk"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "gsi1sk"
      key_type       = "RANGE"
    }
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  tags = var.tags
}
