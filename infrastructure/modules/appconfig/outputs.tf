output "application_id" {
  value = aws_appconfig_application.this.id
}

output "agents" {
  description = "Per-agent AppConfig identifiers and the ARN used to scope IAM."
  value = {
    for id in keys(var.prompts) : id => {
      environment_id = aws_appconfig_environment.agent[id].environment_id
      profile_id     = aws_appconfig_configuration_profile.prompt[id].configuration_profile_id
      arn = join("/", [
        "arn:aws:appconfig:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:application",
        aws_appconfig_application.this.id,
        "environment", aws_appconfig_environment.agent[id].environment_id,
        "configuration", aws_appconfig_configuration_profile.prompt[id].configuration_profile_id,
      ])
    }
  }
}
