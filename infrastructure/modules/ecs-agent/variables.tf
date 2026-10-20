variable "name_prefix" {
  description = "Prefix for named resources, e.g. agentes-sas-dev."
  type        = string
}

variable "agent_id" {
  description = "Stable employee identifier, e.g. accountant."
  type        = string
}

variable "agent_role" {
  description = "Human-readable role, e.g. Accountant."
  type        = string
}

variable "department" {
  type = string
}

variable "reports_to" {
  description = "Manager agent id; empty for the CEO."
  type        = string
  default     = ""
}

variable "cluster_name" {
  type = string
}

variable "image" {
  description = "Hermes agent image URI. The same image is used for every employee."
  type        = string
}

variable "cpu" {
  type    = number
  default = 512
}

variable "memory" {
  type    = number
  default = 1024
}

variable "running" {
  description = "Run the agent's Express service, or remove it (identity, inbox and state are kept)."
  type        = bool
  default     = true
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "execution_role_arn" {
  type = string
}

variable "infrastructure_role_arn" {
  description = "Role ECS Express Mode uses to manage the load balancer, certificate and scaling."
  type        = string
}

variable "queue_arn" {
  type = string
}

variable "queue_url" {
  type = string
}

variable "appconfig" {
  description = "AppConfig identifiers for this agent's prompt."
  type = object({
    application_id = string
    environment_id = string
    profile_id     = string
    arn            = string
  })
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "bedrock_model_id" {
  description = "Bedrock model or inference profile id passed to Hermes."
  type        = string
}

variable "bedrock_role_arn" {
  description = "Role in another account that Hermes assumes to invoke Bedrock. Empty: invoke in this account with the task role."
  type        = string
  default     = ""
}

variable "bedrock_resource_arns" {
  description = "Inference profile and foundation model ARNs the agent may invoke."
  type        = list(string)
}

variable "company_mcp_url" {
  description = "HTTPS base URL of the Company MCP Express service; the in-image proxy forwards to it."
  type        = string
}

variable "mcp_audience" {
  description = "Audience the Company MCP expects in signed tokens."
  type        = string
}

variable "mcp_proxy_port" {
  type    = number
  default = 9000
}

variable "health_port" {
  description = "Port of the runtime's /health endpoint (the load balancer health check)."
  type        = number
  default     = 8080
}

variable "agent_activity_url" {
  description = "agent-activity service endpoint (observability only). Empty disables publishing."
  type        = string
  default     = ""
}

variable "log_level" {
  type    = string
  default = "INFO"
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "extra_environment" {
  type    = map(string)
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
