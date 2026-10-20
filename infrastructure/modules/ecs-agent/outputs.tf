output "service_name" {
  value = module.service.service_name
}

output "task_role_arn" {
  value = aws_iam_role.task.arn
}

output "log_group" {
  value = module.service.log_group
}
