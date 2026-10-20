output "url" {
  description = "Public HTTPS URL of the store (basic auth)."
  value       = module.service.url
}

output "password_parameter" {
  value = aws_ssm_parameter.password.name
}
