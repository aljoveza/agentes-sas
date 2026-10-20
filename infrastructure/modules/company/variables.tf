variable "name" {
  description = "Project name used as a resource prefix."
  type        = string
  default     = "agentes-sas"
}

variable "environment" {
  description = "Environment name used in resource names, e.g. dev."
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "prompts_dir" {
  description = "Directory containing the role prompt YAML files."
  type        = string
}

variable "bedrock_model_id" {
  description = "Bedrock inference profile id Hermes should use, e.g. a us.anthropic.* profile."
  type        = string
}

variable "bedrock_role_arn" {
  description = "Bedrock-only role in another account the agents assume for inference (for an account without Bedrock quota). Empty: this account."
  type        = string
  default     = ""
}

variable "agent_image_tag" {
  description = "Immutable image tag (e.g. git sha) of the Hermes agent image."
  type        = string
}

variable "company_mcp_image_tag" {
  description = "Immutable image tag (e.g. git sha) of the Company MCP image."
  type        = string
}

variable "agent_activity_image_tag" {
  description = "Immutable image tag (e.g. git sha) of the agent-activity image."
  type        = string
}

variable "nexo_image_tag" {
  description = "Immutable image tag (e.g. git sha) of the Nexo image."
  type        = string
}

variable "company_mcp_port" {
  type    = number
  default = 8080
}

variable "running" {
  description = "Create the Express Mode services. False removes them (and their load balancers) between demos; queues, tables, buckets, roles and prompts are kept."
  type        = bool
  default     = true
}

variable "agent_cpu" {
  description = "Agent task CPU units. Each turn starts a Hermes process: at 256 that alone took ~60 s, at 512 it is much faster."
  type        = number
  default     = 512
}

variable "agent_memory" {
  type    = number
  default = 1024
}

variable "container_insights" {
  description = "ECS Container Insights on the cluster (~$0.07 per metric per month)."
  type        = bool
  default     = false
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "archive_retention_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "force_destroy" {
  description = "Allow terraform destroy to remove non-empty buckets and repositories."
  type        = bool
  default     = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
