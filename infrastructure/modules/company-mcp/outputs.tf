output "service_name" {
  value = module.service.service_name
}

output "url" {
  description = "HTTPS base URL (internal load balancer); the MCP endpoint is <url>/mcp."
  value       = module.service.url
}

output "log_group" {
  value = module.service.log_group
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}
