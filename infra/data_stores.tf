# PostgreSQL (RDS), Redis (ElastiCache), and an encrypted S3 bucket for KYC/docs.
# All encrypted at rest, all private (no public access).

# --- KMS key for data-at-rest encryption ---
resource "aws_kms_key" "data" {
  description             = "${local.name} data encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
}

# --- PostgreSQL ---
resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-db"
  subnet_ids = module.vpc.private_subnets
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_instance" "postgres" {
  identifier     = "${local.name}-pg"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 4
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.data.arn

  db_name  = "sokopay"
  username = "sokopay"
  password = random_password.db.result

  multi_az               = true # high availability (standby in a second AZ)
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  backup_retention_period   = 14
  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-pg-final"

  # Cross-region snapshot copy for DR is configured separately (see README).
}

# --- Redis (cache, rate limiting, OTP store, Celery broker) ---
resource "aws_elasticache_subnet_group" "this" {
  name       = "${local.name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${local.name}-redis"
  description                = "${local.name} Redis"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2 # primary + replica
  automatic_failover_enabled = true
  multi_az_enabled           = true

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.redis.id]
}

# --- S3 bucket for KYC documents, audit archives, etc. ---
resource "aws_s3_bucket" "docs" {
  bucket = "${local.name}-docs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs" {
  bucket = aws_s3_bucket.docs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}
