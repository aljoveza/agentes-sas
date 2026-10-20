variable "name" {
  description = "AppConfig application name."
  type        = string
}

variable "prompts" {
  description = "Prompt YAML file path keyed by agent id."
  type        = map(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
