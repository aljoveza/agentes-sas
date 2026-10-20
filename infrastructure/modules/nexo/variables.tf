variable "name_prefix" {
  type = string
}

variable "running" {
  type    = bool
  default = true
}

variable "cluster_name" {
  type = string
}

variable "image" {
  type = string
}

variable "port" {
  type    = number
  default = 8090
}

variable "execution_role_arn" {
  type = string
}

variable "execution_role_name" {
  description = "Shared task execution role; gets read access to the password parameter."
  type        = string
}

variable "infrastructure_role_arn" {
  type = string
}

variable "task_role_arn" {
  description = "The order simulator's role: the identity the Company MCP sees for Nexo's orders."
  type        = string
}

variable "task_role_name" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "company_mcp_url" {
  type = string
}

variable "mcp_audience" {
  type = string
}

variable "agent_activity_url" {
  type = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "business_events_log_group" {
  type = string
}

variable "business_events_log_group_arn" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
