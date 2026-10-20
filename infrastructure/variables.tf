variable "region" {
  type    = string
  default = "us-east-1"
}

variable "bedrock_model_id" {
  description = "Bedrock inference profile id for Hermes."
  type        = string
}

variable "bedrock_role_arn" {
  description = "Bedrock-only role in another account (e.g. aws-management's bedrock_invoker_role_arn output) the agents assume for inference. Empty: this account."
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

variable "running" {
  description = "Create the ECS Express Mode services. Keep false until the images are pushed, and between demos."
  type        = bool
  default     = false
}
