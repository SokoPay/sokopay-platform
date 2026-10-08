output "alb_dns_name" {
  description = "Point your DNS (CNAME) for the API/portal host at this."
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  description = "Push the backend image here, then set var.container_image to the tag."
  value       = aws_ecr_repository.backend.repository_url
}

output "rds_address" {
  value = aws_db_instance.postgres.address
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "docs_bucket" {
  value = aws_s3_bucket.docs.bucket
}

output "dr_docs_bucket" {
  description = "DR copy of the docs bucket (null when enable_dr = false)."
  value       = var.enable_dr ? aws_s3_bucket.docs_dr[0].bucket : null
}

output "app_secret_arn" {
  description = "Secrets Manager secret holding app config; set partner creds here."
  value       = aws_secretsmanager_secret.app.arn
}

# --- used by .github/workflows/deploy.yml ---
output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "api_task_definition_family" {
  value = aws_ecs_task_definition.api.family
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "private_subnet_ids" {
  value = module.vpc.private_subnets
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}
