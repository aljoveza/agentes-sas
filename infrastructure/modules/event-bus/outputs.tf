output "name" {
  value = aws_cloudwatch_event_bus.this.name
}

output "arn" {
  value = aws_cloudwatch_event_bus.this.arn
}

output "archive_arn" {
  value = aws_cloudwatch_event_archive.all.arn
}

output "audit_log_group" {
  value = aws_cloudwatch_log_group.audit.name
}

output "audit_log_group_arn" {
  value = aws_cloudwatch_log_group.audit.arn
}
