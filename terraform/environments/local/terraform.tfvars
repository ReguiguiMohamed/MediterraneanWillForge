# terraform/environments/local/terraform.tfvars
# ──────────────────────────────────────────────
# Local development defaults. Safe to commit — all values are well-known
# dev credentials with no production exposure.

environment            = "local"
project_name           = "med-ops-fortress"

minio_access_key       = "minioadmin"
minio_secret_key       = "minioadmin"
grafana_admin_password = "fortress"

prometheus_retention_days = 30

lakehouse_buckets = {
  bronze = "bronze"
  silver = "silver"
  gold   = "gold"
}
