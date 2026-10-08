locals {
  name = "${var.project}-${var.environment}"
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Networking — a VPC with public (ALB, NAT) and private (app, data) subnets
# across two AZs. Uses the well-maintained community VPC module.
# ---------------------------------------------------------------------------
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = "${local.name}-vpc"
  cidr = var.vpc_cidr
  azs  = var.azs

  public_subnets  = [for i, _ in var.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnets = [for i, _ in var.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway     = true
  single_nat_gateway     = var.single_nat_gateway # false → one NAT per AZ (HA)
  one_nat_gateway_per_az = !var.single_nat_gateway

  enable_dns_hostnames = true
  enable_dns_support   = true
}
