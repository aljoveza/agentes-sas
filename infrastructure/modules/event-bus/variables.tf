variable "name" {
  description = "Event bus name."
  type        = string
}

variable "subscriptions" {
  description = "Inboxes keyed by subscriber (agent id). patterns maps a route name to an EventBridge event pattern (JSON)."
  type = map(object({
    queue_arn = string
    queue_url = string
    patterns  = map(string)
  }))

  validation {
    condition     = alltrue([for sub in values(var.subscriptions) : length(sub.patterns) > 0])
    error_message = "Every subscription needs at least one event pattern."
  }
}

variable "archive_retention_days" {
  type    = number
  default = 7
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
