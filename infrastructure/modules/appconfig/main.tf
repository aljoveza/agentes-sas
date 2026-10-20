# Job descriptions as configuration. Each employee gets its own AppConfig
# environment because AppConfig allows only one in-flight deployment per
# environment; this also lets one employee's prompt roll out independently.

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

resource "aws_appconfig_application" "this" {
  name        = var.name
  description = "Agent role prompts (job descriptions)."

  tags = var.tags
}

resource "aws_appconfig_environment" "agent" {
  for_each = var.prompts

  name           = each.key
  application_id = aws_appconfig_application.this.id

  tags = var.tags
}

resource "aws_appconfig_configuration_profile" "prompt" {
  for_each = var.prompts

  name           = "${each.key}-prompt"
  application_id = aws_appconfig_application.this.id
  location_uri   = "hosted"
  type           = "AWS.Freeform"

  tags = var.tags
}

resource "aws_appconfig_hosted_configuration_version" "prompt" {
  for_each = var.prompts

  application_id           = aws_appconfig_application.this.id
  configuration_profile_id = aws_appconfig_configuration_profile.prompt[each.key].configuration_profile_id
  content_type             = "application/x-yaml"
  content                  = file(each.value)
  description              = "prompt version ${try(yamldecode(file(each.value)).version, "unknown")}"

  lifecycle {
    precondition {
      condition     = alltrue([for key in ["version", "role", "department", "allowed_tools"] : can(yamldecode(file(each.value))[key])])
      error_message = "Prompt ${each.value} must define version, role, department and allowed_tools."
    }
  }
}

resource "aws_appconfig_deployment_strategy" "immediate" {
  name                           = "${var.name}-immediate"
  description                    = "Deploy prompt changes at once; the demo has one task per agent."
  deployment_duration_in_minutes = 0
  growth_factor                  = 100
  final_bake_time_in_minutes     = 0
  replicate_to                   = "NONE"

  tags = var.tags
}

resource "aws_appconfig_deployment" "prompt" {
  for_each = var.prompts

  application_id           = aws_appconfig_application.this.id
  environment_id           = aws_appconfig_environment.agent[each.key].environment_id
  configuration_profile_id = aws_appconfig_configuration_profile.prompt[each.key].configuration_profile_id
  configuration_version    = tostring(aws_appconfig_hosted_configuration_version.prompt[each.key].version_number)
  deployment_strategy_id   = aws_appconfig_deployment_strategy.immediate.id

  tags = var.tags
}
