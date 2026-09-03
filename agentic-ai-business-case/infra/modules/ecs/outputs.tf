output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

# Family only (no revision) so RunTask always picks up the newest revision.
output "task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "container_name" {
  description = "Needed by RunTask to target command overrides at the right container."
  value       = "${var.client_name}-business-case-generator"
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
