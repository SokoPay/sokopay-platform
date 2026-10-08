variable "project" {
  type    = string
  default = "sokopay"
}

variable "environment" {
  type        = string
  description = "e.g. staging, production"
  default     = "production"
}

variable "region" {
  type        = string
  description = "AWS region. Cape Town is the nearest region to Ghana."
  default     = "af-south-1"
}

variable "azs" {
  type        = list(string)
  description = "Availability zones to spread across (Multi-AZ)."
  default     = ["af-south-1a", "af-south-1b"]
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "container_image" {
  type        = string
  description = "ECR image URI for the backend (api + worker share it, differ by command)."
}

variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate for the ALB HTTPS listener (payments require TLS)."
}

variable "allowed_hosts" {
  type        = string
  description = "Django ALLOWED_HOSTS (comma-separated)."
}

variable "active_licence" {
  type        = string
  description = "The BoG licence this deployment operates under (the licence gate)."
  default     = "PSP_STANDARD"
}

variable "rail_provider" {
  type    = string
  default = "korba"
}

variable "sms_provider" {
  type    = string
  default = "hubtel"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

# --- Autoscaling ---
variable "api_min_count" {
  type    = number
  default = 2
}

variable "api_max_count" {
  type    = number
  default = 10
}

variable "api_requests_per_task" {
  type        = number
  description = "Target ALB requests per API task (per minute) before scaling out."
  default     = 500
}

variable "worker_max_count" {
  type        = number
  description = "Upper bound for Celery workers (beat runs separately, so >1 is safe)."
  default     = 4
}

# --- High availability / disaster recovery ---
variable "single_nat_gateway" {
  type        = bool
  description = "true = one NAT (cheaper); false = one NAT per AZ (survives an AZ outage)."
  default     = true
}

variable "enable_dr" {
  type        = bool
  description = <<-EOT
    Copy database backups and KYC documents to a second AWS region. This sends copies
    of Ghanaian customer data outside Africa (AWS has only one African region), so
    confirm the data-residency position with BoG / counsel before turning it on.
  EOT
  default     = false
}

variable "dr_region" {
  type        = string
  description = "Region for DR copies when enable_dr = true."
  default     = "eu-west-1"
}

variable "dr_backup_retention_days" {
  type    = number
  default = 14
}
