# Mediterranean Ops Fortress — Root Terraform configuration
# Orchestrates all modules. The infrastructure is forged here, not wished into existence.

provider "docker" {
  host = "unix:///var/run/docker.sock"
}

# ── Networking module ──────────────────────────────────────────────────────────
module "networking" {
  source       = "./modules/networking"
  project_name = var.project_name
  environment  = var.environment
}

# ── Storage module (MinIO + volumes) ──────────────────────────────────────────
module "storage" {
  source       = "./modules/storage"
  project_name = var.project_name
  environment  = var.environment
  network_id   = module.networking.fortress_network_id

  minio_access_key  = var.minio_access_key
  minio_secret_key  = var.minio_secret_key
  lakehouse_buckets = var.lakehouse_buckets
}

# ── Compute module (Prometheus, Grafana, Alertmanager, Pushgateway) ───────────
module "compute" {
  source       = "./modules/compute"
  project_name = var.project_name
  environment  = var.environment
  network_id   = module.networking.fortress_network_id

  grafana_admin_password    = var.grafana_admin_password
  prometheus_retention_days = var.prometheus_retention_days
}
