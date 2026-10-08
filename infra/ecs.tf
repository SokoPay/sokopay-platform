# ECS Fargate, three services from one image (differing only by command):
#   api    — Django behind the ALB (autoscaled)
#   worker — Celery workers that execute tasks (autoscaled)
#   beat   — the Celery scheduler that enqueues periodic jobs. Exactly ONE must run:
#            two would fire every scheduled job (settlements, reconciliation) twice.

locals {
  # Non-secret configuration, passed as plain environment variables.
  container_env = [
    { name = "DJANGO_SETTINGS_MODULE", value = "config.settings.prod" },
    { name = "ALLOWED_HOSTS", value = var.allowed_hosts },
    { name = "SOKOPAY_ACTIVE_LICENCE", value = var.active_licence },
    { name = "RAIL_PROVIDER", value = var.rail_provider },
    { name = "SMS_PROVIDER", value = var.sms_provider },
  ]

  # Secrets, pulled from Secrets Manager by key at task start.
  secret_keys = [
    "SECRET_KEY", "FIELD_ENCRYPTION_KEY", "DATABASE_URL", "REDIS_URL",
    "RAIL_API_KEY", "RAIL_WEBHOOK_SECRET", "HUBTEL_CLIENT_ID", "HUBTEL_CLIENT_SECRET",
  ]
  container_secrets = [
    for k in local.secret_keys : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::"
    }
  ]
}

resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name}"
  retention_in_days = 90
}

# --- IAM: execution role (pull image, read secrets) + task role (app AWS access) ---
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to read the app secret and decrypt it.
data "aws_iam_policy_document" "exec_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app.arn]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "exec_secrets" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.exec_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

# The app itself may read/write the KYC/docs bucket.
data "aws_iam_policy_document" "task_app" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_role_policy" "task_app" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_app.json
}

# --- Task definitions ---
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = var.container_image
    essential    = true
    command      = ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3"]
    portMappings = [{ containerPort = 8000 }]
    environment  = local.container_env
    secrets      = local.container_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name        = "worker"
    image       = var.container_image
    essential   = true
    command     = ["celery", "-A", "config", "worker", "-l", "info"]
    environment = local.container_env
    secrets     = local.container_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

# --- Services ---
resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.app.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  # Blue/green-ish rolling deploy with health gating.
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # The autoscaler owns the running count after creation (see autoscaling.tf).
  lifecycle { ignore_changes = [desired_count] }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.app.id]
  }

  lifecycle { ignore_changes = [desired_count] }
}

# --- Celery beat: the single scheduler ---
resource "aws_ecs_task_definition" "beat" {
  family                   = "${local.name}-beat"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name        = "beat"
    image       = var.container_image
    essential   = true
    command     = ["celery", "-A", "config", "beat", "-l", "info"]
    environment = local.container_env
    secrets     = local.container_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "beat"
      }
    }
  }])
}

resource "aws_ecs_service" "beat" {
  name            = "${local.name}-beat"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.beat.arn
  desired_count   = 1 # never more: duplicate schedulers duplicate jobs
  launch_type     = "FARGATE"

  # Stop the old scheduler before starting the new one, so two never overlap during a
  # deploy. A tick missed in that gap is the safer failure: the 5-minute pollers run
  # again shortly, and a missed daily job (reconciliation, settlements) can be re-run
  # by hand — e.g. run_daily_reconciliation(date="YYYY-MM-DD").
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets         = module.vpc.private_subnets
    security_groups = [aws_security_group.app.id]
  }
  # Deliberately NOT autoscaled.
}
