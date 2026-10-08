# Monitoring and alerting.
#
# Two kinds of signal:
#  1. Infrastructure: load balancer errors and latency, ECS CPU/memory, the beat
#     scheduler running, RDS and Redis health.
#  2. Business: the app logs one JSON line every 5 minutes ({"metric": "ops", ...},
#     apps/common/health.py). Metric filters turn each field into a CloudWatch metric
#     in the SokoPay/Ops namespace; alarms page when money is stuck, safeguarding is
#     short, or errors spike.
# Alarms notify an SNS topic; subscribe the on-call emails (and an SMS/pager endpoint).

variable "alert_emails" {
  type        = list(string)
  default     = []
  description = "Who gets alarm emails (each must confirm the SNS subscription)."
}

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "email" {
  for_each  = toset(var.alert_emails)
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  alarm_actions = [aws_sns_topic.alerts.arn]
  ops_ns        = "SokoPay/Ops"

  # Business metrics from the ops snapshot: name => alarm when the value is >= threshold.
  ops_alarms = {
    stuck_settlements            = { threshold = 1, periods = 1, help = "Settlement payouts stuck (no partner answer). Approvals > Stuck payouts." }
    stuck_refunds                = { threshold = 1, periods = 1, help = "MoMo refunds stuck at the partner." }
    transfers_pending_over_1h    = { threshold = 1, periods = 3, help = "Transfers to other networks pending > 1h." }
    cross_border_pending_over_1h = { threshold = 1, periods = 3, help = "Cross-border sends pending > 1h." }
    lifestyle_pending_over_1h    = { threshold = 1, periods = 3, help = "Ticket/food orders pending > 1h." }
    aml_high_open                = { threshold = 1, periods = 12, help = "High-severity AML alert open for 1h+." }
    disputes_overdue             = { threshold = 1, periods = 12, help = "Customer disputes past the merchant deadline." }
    withdrawals_waiting_over_24h = { threshold = 1, periods = 1, help = "Provider withdrawals waiting > 24h." }
    safeguarding                 = { threshold = 2, periods = 1, help = "SAFEGUARDING SHORTFALL: e-money exceeds trust funds." }
    webhooks_abandoned_1h        = { threshold = 20, periods = 1, help = "Many merchant webhooks given up in the last hour." }
  }
}

# --- business metrics from the JSON log line ---------------------------------------------------
resource "aws_cloudwatch_log_metric_filter" "ops" {
  for_each       = local.ops_alarms
  name           = "${local.name}-ops-${each.key}"
  log_group_name = aws_cloudwatch_log_group.backend.name
  pattern        = "{ $.metric = \"ops\" }"
  metric_transformation {
    name      = each.key
    namespace = local.ops_ns
    value     = "$.${each.key}"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "ops" {
  for_each            = local.ops_alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.help
  namespace           = local.ops_ns
  metric_name         = each.key
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = each.value.periods
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  depends_on          = [aws_cloudwatch_log_metric_filter.ops]
}

# The snapshot itself must keep arriving: if beat or the worker dies, nothing above fires.
resource "aws_cloudwatch_metric_alarm" "ops_heartbeat" {
  alarm_name          = "${local.name}-ops-snapshot-missing"
  alarm_description   = "No ops snapshot for 20 minutes: Celery beat or worker is down (payouts and polls have stopped)."
  namespace           = local.ops_ns
  metric_name         = "stuck_settlements"
  statistic           = "SampleCount"
  period              = 600
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  depends_on          = [aws_cloudwatch_log_metric_filter.ops]
}

# Errors and criticals in the application logs.
resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each       = { error = "ERROR", critical = "CRITICAL" }
  name           = "${local.name}-log-${each.key}"
  log_group_name = aws_cloudwatch_log_group.backend.name
  pattern        = "{ $.level = \"${each.value}\" }"
  metric_transformation {
    name      = "log_${each.key}"
    namespace = local.ops_ns
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "log_critical" {
  alarm_name          = "${local.name}-log-critical"
  alarm_description   = "A CRITICAL log line (e.g. stuck payouts, safeguarding)."
  namespace           = local.ops_ns
  metric_name         = "log_critical"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  depends_on          = [aws_cloudwatch_log_metric_filter.errors]
}

resource "aws_cloudwatch_metric_alarm" "log_errors" {
  alarm_name          = "${local.name}-log-errors"
  alarm_description   = "More than 25 application errors in 5 minutes."
  namespace           = local.ops_ns
  metric_name         = "log_error"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 25
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  depends_on          = [aws_cloudwatch_log_metric_filter.errors]
}

# --- load balancer ----------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  alarm_description   = "API returning server errors."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 20
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "alb_latency" {
  alarm_name          = "${local.name}-api-slow"
  alarm_description   = "API p95 response time above 2 seconds."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = 2
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy" {
  alarm_name          = "${local.name}-api-unhealthy-targets"
  alarm_description   = "API tasks failing the health check."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = local.alarm_actions
}

# --- ECS ----------------------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "ecs_cpu" {
  for_each            = { api = aws_ecs_service.api.name, worker = aws_ecs_service.worker.name }
  alarm_name          = "${local.name}-${each.key}-cpu"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = each.value }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory" {
  for_each            = { api = aws_ecs_service.api.name, worker = aws_ecs_service.worker.name }
  alarm_name          = "${local.name}-${each.key}-memory"
  namespace           = "AWS/ECS"
  metric_name         = "MemoryUtilization"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = each.value }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "beat_running" {
  alarm_name          = "${local.name}-beat-not-running"
  alarm_description   = "The scheduler (Celery beat) isn't running: settlements, polls and recon have stopped."
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.beat.name }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
}

# --- RDS ----------------------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "rds" {
  for_each = {
    cpu         = { metric = "CPUUtilization", threshold = 80, op = "GreaterThanThreshold", stat = "Average" }
    storage     = { metric = "FreeStorageSpace", threshold = 10737418240, op = "LessThanThreshold", stat = "Minimum" }
    memory      = { metric = "FreeableMemory", threshold = 268435456, op = "LessThanThreshold", stat = "Minimum" }
    connections = { metric = "DatabaseConnections", threshold = 150, op = "GreaterThanThreshold", stat = "Maximum" }
  }
  alarm_name          = "${local.name}-db-${each.key}"
  namespace           = "AWS/RDS"
  metric_name         = each.value.metric
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.identifier }
  statistic           = each.value.stat
  period              = 300
  evaluation_periods  = 3
  threshold           = each.value.threshold
  comparison_operator = each.value.op
  alarm_actions       = local.alarm_actions
}

# --- Redis --------------------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name}-redis-memory"
  alarm_description   = "Redis memory above 80% (rate limits, OTPs and the Celery queue live here)."
  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  dimensions          = { ReplicationGroupId = aws_elasticache_replication_group.redis.id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = local.alarm_actions
}

# --- one dashboard ------------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "ops" {
  dashboard_name = "${local.name}-operations"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "Money needing attention", region = var.region, stat = "Maximum", period = 300
          metrics = [for k in ["stuck_settlements", "stuck_refunds", "transfers_pending_over_1h", "cross_border_pending_over_1h", "withdrawals_waiting_over_24h"] : [local.ops_ns, k]]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title   = "Payments (15 min)", region = var.region, stat = "Maximum", period = 300
          metrics = [[local.ops_ns, "payments_15m"], [local.ops_ns, "payments_failed_15m"]]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title   = "Compliance", region = var.region, stat = "Maximum", period = 300
          metrics = [[local.ops_ns, "aml_high_open"], [local.ops_ns, "disputes_overdue"], [local.ops_ns, "safeguarding"]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "API", region = var.region, period = 300
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "Sum" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.this.arn_suffix, { stat = "p95", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 24, height = 6
        properties = {
          title = "Database", region = var.region, period = 300
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.postgres.identifier],
            ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.postgres.identifier, { yAxis = "right" }],
          ]
        }
      },
    ]
  })
}

output "alerts_topic_arn" {
  description = "Add more subscribers (SMS, PagerDuty/Opsgenie HTTPS) to this topic."
  value       = aws_sns_topic.alerts.arn
}
