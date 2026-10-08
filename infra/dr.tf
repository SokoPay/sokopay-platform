# Disaster recovery: keep copies of the two things we cannot rebuild — the database
# and the KYC/document store — in a second AWS region.
#
#   * RDS automated backups are replicated cross-region (point-in-time restore there).
#   * The docs bucket replicates every object version (and deletes) cross-region.
#
# Everything here is created only when var.enable_dr = true. See the variable's
# description: turning it on moves copies of customer data outside Africa, which is a
# data-residency decision for the business and BoG, not just an engineering one.
#
# Targets (see README): RPO < 1h (backups replicate continuously; S3 within minutes),
# RTO < 4h (restore RDS from the replicated backups, repoint the app via Terraform).

locals {
  dr_count = var.enable_dr ? 1 : 0
}

# --- Encryption key in the DR region ---
resource "aws_kms_key" "dr" {
  count                   = local.dr_count
  provider                = aws.dr
  description             = "${local.name} DR encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
}

# --- Database: cross-region automated backup replication ---
resource "aws_db_instance_automated_backups_replication" "postgres" {
  count                  = local.dr_count
  provider               = aws.dr
  source_db_instance_arn = aws_db_instance.postgres.arn
  kms_key_id             = aws_kms_key.dr[0].arn
  retention_period       = var.dr_backup_retention_days
}

# --- Documents: destination bucket in the DR region ---
resource "aws_s3_bucket" "docs_dr" {
  count    = local.dr_count
  provider = aws.dr
  bucket   = "${local.name}-docs-dr-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "docs_dr" {
  count                   = local.dr_count
  provider                = aws.dr
  bucket                  = aws_s3_bucket.docs_dr[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "docs_dr" {
  count    = local.dr_count
  provider = aws.dr
  bucket   = aws_s3_bucket.docs_dr[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "docs_dr" {
  count    = local.dr_count
  provider = aws.dr
  bucket   = aws_s3_bucket.docs_dr[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.dr[0].arn
    }
  }
}

# --- Replication role: S3 reads from the source and writes to the DR bucket ---
data "aws_iam_policy_document" "s3_replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "s3_replication" {
  count              = local.dr_count
  name               = "${local.name}-s3-replication"
  assume_role_policy = data.aws_iam_policy_document.s3_replication_assume.json
}

data "aws_iam_policy_document" "s3_replication" {
  count = local.dr_count

  statement {
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.docs.arn]
  }
  statement {
    actions = [
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]
    resources = ["${aws_s3_bucket.docs.arn}/*"]
  }
  statement {
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.docs_dr[0].arn}/*"]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn] # source objects
  }
  statement {
    actions   = ["kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.dr[0].arn] # replicas
  }
}

resource "aws_iam_role_policy" "s3_replication" {
  count  = local.dr_count
  role   = aws_iam_role.s3_replication[0].id
  policy = data.aws_iam_policy_document.s3_replication[0].json
}

# --- Replication rule on the source docs bucket ---
resource "aws_s3_bucket_replication_configuration" "docs" {
  count  = local.dr_count
  role   = aws_iam_role.s3_replication[0].arn
  bucket = aws_s3_bucket.docs.id

  rule {
    id     = "docs-to-dr"
    status = "Enabled"

    filter {} # all objects

    delete_marker_replication {
      status = "Enabled"
    }

    # Source objects are KMS-encrypted, so replication must opt in to them and
    # re-encrypt with the DR-region key.
    source_selection_criteria {
      sse_kms_encrypted_objects {
        status = "Enabled"
      }
    }

    destination {
      bucket        = aws_s3_bucket.docs_dr[0].arn
      storage_class = "STANDARD_IA"
      encryption_configuration {
        replica_kms_key_id = aws_kms_key.dr[0].arn
      }
    }
  }

  # Both buckets must be versioned before replication can be configured.
  depends_on = [aws_s3_bucket_versioning.docs, aws_s3_bucket_versioning.docs_dr]
}
