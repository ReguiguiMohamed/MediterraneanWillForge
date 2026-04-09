variable "environment" {
  description = "Deployment environment name (local, staging, prod)"
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: local, staging, prod."
  }
}

variable "project_name" {
  description = "Project identifier used in resource naming"
  type        = string
  default     = "med-ops-fortress"
}

variable "minio_access_key" {
  description = "MinIO root access key"
  type        = string
  sensitive   = true
  default     = "minioadmin"
}

variable "minio_secret_key" {
  description = "MinIO root secret key"
  type        = string
  sensitive   = true
  default     = "minioadmin"
}

variable "grafana_admin_password" {
  description = "Grafana admin password"
  type        = string
  sensitive   = true
  default     = "fortress"
}

variable "lakehouse_buckets" {
  description = "MinIO bucket names for each medallion layer"
  type = object({
    bronze = string
    silver = string
    gold   = string
  })
  default = {
    bronze = "bronze"
    silver = "silver"
    gold   = "gold"
  }
}

variable "prometheus_retention_days" {
  description = "How many days Prometheus retains TSDB data"
  type        = number
  default     = 30
}
