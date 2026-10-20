provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "agentes-sas"
      Environment = "dev"
      ManagedBy   = "terraform"
      CostCenter  = "DevopsDays"
    }
  }
}

module "company" {
  source = "./modules/company"

  environment      = "dev"
  prompts_dir      = "${path.root}/../prompts"
  bedrock_model_id = var.bedrock_model_id
  bedrock_role_arn = var.bedrock_role_arn

  agent_image_tag          = var.agent_image_tag
  company_mcp_image_tag    = var.company_mcp_image_tag
  agent_activity_image_tag = var.agent_activity_image_tag
  nexo_image_tag           = var.nexo_image_tag
  running                  = var.running

  deletion_protection = false
  force_destroy       = true
  log_retention_days  = 7
}
