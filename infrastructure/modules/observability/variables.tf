variable "name_prefix" {
  description = "Dashboard name and saved-query folder, e.g. agentes-sas-dev."
  type        = string
}

variable "agent_ids" {
  type = list(string)
}

variable "log_groups" {
  description = "Log group name per agent id, plus company-mcp."
  type        = map(string)
}

variable "business_events_log_group" {
  type = string
}

variable "queues" {
  description = "Per agent id: inbox and dead-letter queue names."
  type = map(object({
    name     = string
    dlq_name = string
  }))
}

variable "cluster_name" {
  type = string
}

variable "service_names" {
  description = "ECS services shown in the CPU widget."
  type        = list(string)
}
