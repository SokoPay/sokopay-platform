# Application secrets live in Secrets Manager and are injected into the ECS tasks at
# runtime — never baked into images or committed. Django reads them from the
# environment (12-factor), matching backend/config/settings.

resource "random_password" "django_secret_key" {
  length  = 64
  special = false
}

resource "random_password" "field_encryption_key" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name       = "${local.name}/app"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SECRET_KEY           = random_password.django_secret_key.result
    FIELD_ENCRYPTION_KEY = random_password.field_encryption_key.result
    DATABASE_URL = format(
      "postgres://%s:%s@%s:%s/%s",
      aws_db_instance.postgres.username,
      random_password.db.result,
      aws_db_instance.postgres.address,
      aws_db_instance.postgres.port,
      aws_db_instance.postgres.db_name,
    )
    REDIS_URL = format(
      "rediss://%s:6379/0",
      aws_elasticache_replication_group.redis.primary_endpoint_address,
    )
    # Partner / provider credentials are added here once issued (left blank in TF;
    # set via `terraform import` of an out-of-band value, or a separate secret).
    RAIL_API_KEY         = ""
    RAIL_WEBHOOK_SECRET  = ""
    HUBTEL_CLIENT_ID     = ""
    HUBTEL_CLIENT_SECRET = ""
  })

  lifecycle {
    # Don't let TF clobber partner creds that ops set directly in the console.
    ignore_changes = [secret_string]
  }
}
