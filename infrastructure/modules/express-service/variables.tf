variable "name_prefix" {
  description = "Prefix for the log group path, e.g. agentes-sas-dev."
  type        = string
}

variable "name" {
  description = "Service name, unique in the cluster. Changing it replaces the service."
  type        = string
}

variable "enabled" {
  description = "Create the service. False removes it (and its share of the load balancer); everything else is kept."
  type        = bool
  default     = true
}

variable "cluster_name" {
  type = string
}

variable "image" {
  description = "Container image; must be linux/amd64 (Express Mode tasks run on X86_64)."
  type        = string
}

variable "command" {
  type    = list(string)
  default = null
}

variable "port" {
  description = "Container port the load balancer sends traffic and health checks to."
  type        = number
}

variable "health_check_path" {
  type    = string
  default = "/health"
}

variable "cpu" {
  type    = number
  default = 256
}

variable "memory" {
  type    = number
  default = 512
}

variable "min_tasks" {
  type    = number
  default = 1
}

variable "max_tasks" {
  type    = number
  default = 1
}

variable "environment" {
  type    = map(string)
  default = {}
}

variable "secrets" {
  description = "Environment variable name => Secrets Manager or SSM parameter ARN."
  type        = map(string)
  default     = {}
}

variable "execution_role_arn" {
  type = string
}

variable "infrastructure_role_arn" {
  type = string
}

variable "task_role_arn" {
  description = "The service's AWS identity; null for a service that calls no AWS APIs."
  type        = string
  default     = null
}

variable "subnet_ids" {
  description = "At least two subnets. Private subnets: internal load balancer; public: internet-facing."
  type        = list(string)
}

variable "security_group_ids" {
  description = "Task security groups (outbound rules). Express Mode adds the load balancer ingress itself."
  type        = list(string)
}

variable "wait_for_steady_state" {
  type    = bool
  default = false
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
