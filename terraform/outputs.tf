output "minio_endpoint" {
  description = "MinIO S3-compatible API endpoint"
  value       = "http://localhost:${module.storage.minio_api_port}"
}

output "minio_console_url" {
  description = "MinIO Web Console URL"
  value       = "http://localhost:${module.storage.minio_console_port}"
}

output "prometheus_url" {
  description = "Prometheus UI URL"
  value       = "http://localhost:${module.compute.prometheus_port}"
}

output "grafana_url" {
  description = "Grafana UI URL"
  value       = "http://localhost:${module.compute.grafana_port}"
}

output "alertmanager_url" {
  description = "Alertmanager UI URL"
  value       = "http://localhost:${module.compute.alertmanager_port}"
}

output "pushgateway_url" {
  description = "Prometheus Pushgateway endpoint"
  value       = "http://localhost:${module.compute.pushgateway_port}"
}
