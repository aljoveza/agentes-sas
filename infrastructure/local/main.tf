# The business and state planes on LocalStack, for local development. Same modules
# and the same org chart as AWS, so routing rules match; compute (ECS), AppConfig and
# Bedrock are not emulated — agents and the MCP run as local containers instead.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # local/compose.yaml keeps state in a volume: terraform init -backend-config=path=...
  backend "local" {}
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    dynamodb = var.localstack_endpoint
    events   = var.localstack_endpoint
    iam      = var.localstack_endpoint
    logs     = var.localstack_endpoint
    s3       = var.localstack_endpoint
    sqs      = var.localstack_endpoint
    sts      = var.localstack_endpoint
  }

  default_tags {
    tags = {
      Project   = "agentes-sas"
      ManagedBy = "terraform"
    }
  }
}

variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

locals {
  prefix = "agentes-sas-local"
}

module "organization" {
  source = "../modules/organization"
}

module "inbox" {
  source   = "../modules/queue"
  for_each = module.organization.agents

  name = "${local.prefix}-${each.key}"
  # Short timeout locally so failed agent runs are retried quickly.
  visibility_timeout_seconds = 300
}

module "event_bus" {
  source = "../modules/event-bus"

  name = "${local.prefix}-business"

  subscriptions = {
    for id, _ in module.organization.agents : id => {
      queue_arn = module.inbox[id].arn
      queue_url = module.inbox[id].url
      patterns  = module.organization.event_routes[id]
    }
  }
}

module "ledger" {
  source = "../modules/dynamodb"

  name                = "${local.prefix}-company"
  deletion_protection = false
}

module "documents" {
  source = "../modules/s3"

  name          = "${local.prefix}-company"
  force_destroy = true
}

output "event_bus_name" {
  value = module.event_bus.name
}

output "table_name" {
  value = module.ledger.name
}

output "bucket_name" {
  value = module.documents.name
}

output "inboxes" {
  value = { for id, q in module.inbox : id => q.url }
}
