# Security groups: internet → ALB → app → data stores. Each tier only accepts
# traffic from the tier in front of it.

resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  description = "ALB: accepts HTTPS (and HTTP for redirect) from the internet"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "HTTP (redirected to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "app" {
  name_prefix = "${local.name}-app-"
  description = "ECS tasks: accept app traffic only from the ALB"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "App port from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "db" {
  name_prefix = "${local.name}-db-"
  description = "PostgreSQL: accept only from the app tier"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_security_group" "redis" {
  name_prefix = "${local.name}-redis-"
  description = "Redis: accept only from the app tier"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  lifecycle { create_before_destroy = true }
}
