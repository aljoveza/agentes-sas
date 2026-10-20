# One ECS Express Mode service. From an image, a port and a health path, ECS creates
# the Fargate service, task definition, an HTTPS Application Load Balancer (shared by
# up to 25 Express services in the VPC), certificate, target group, security groups,
# auto scaling and a deployment alarm, and rolls out every change as a canary.
#
# Subnets decide exposure: private subnets get an internal load balancer, public
# subnets an internet-facing one.

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name_prefix}/${var.name}"
  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_ecs_express_gateway_service" "this" {
  count = var.enabled ? 1 : 0

  service_name            = var.name
  cluster                 = var.cluster_name
  execution_role_arn      = var.execution_role_arn
  infrastructure_role_arn = var.infrastructure_role_arn
  task_role_arn           = var.task_role_arn
  cpu                     = tostring(var.cpu)
  memory                  = tostring(var.memory)
  health_check_path       = var.health_check_path
  wait_for_steady_state   = var.wait_for_steady_state

  primary_container {
    image          = var.image
    container_port = var.port
    command        = var.command

    aws_logs_configuration {
      log_group         = aws_cloudwatch_log_group.this.name
      log_stream_prefix = var.name
    }

    dynamic "environment" {
      for_each = var.environment

      content {
        name  = environment.key
        value = environment.value
      }
    }

    dynamic "secret" {
      for_each = var.secrets

      content {
        name       = secret.key
        value_from = secret.value
      }
    }
  }

  network_configuration {
    subnets         = var.subnet_ids
    security_groups = var.security_group_ids
  }

  # Express fills in the metric and target when they are omitted, which the provider
  # then reports as an inconsistent result: declare them.
  scaling_target {
    min_task_count            = var.min_tasks
    max_task_count            = var.max_tasks
    auto_scaling_metric       = "AVERAGE_CPU"
    auto_scaling_target_value = 60
  }

  tags = var.tags
}

locals {
  endpoint = try(aws_ecs_express_gateway_service.this[0].ingress_paths[0].endpoint, "")
}
