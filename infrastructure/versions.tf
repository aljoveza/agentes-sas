terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # The state bucket is created outside Terraform; its settings live in backend.conf:
  #   terraform init -backend-config=backend.conf
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
