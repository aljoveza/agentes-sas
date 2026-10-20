variable "name" {
  description = "Queue name. The DLQ is named <name>-dlq."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a message stays hidden while an agent works on it. LLM turns can take minutes."
  type        = number
  default     = 900
}

variable "message_retention_seconds" {
  description = "How long undelivered work is buffered (e.g. while an agent is down)."
  type        = number
  default     = 345600
}

variable "max_receive_count" {
  description = "Deliveries before a message is moved to the DLQ."
  type        = number
  default     = 3
}

variable "tags" {
  type    = map(string)
  default = {}
}
