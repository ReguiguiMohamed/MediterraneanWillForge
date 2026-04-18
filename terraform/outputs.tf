output "vm_public_ip" {
  description = "Public IP of the fortress VM — use this in ansible/inventory/hosts.ini"
  value       = module.compute.public_ip
}

output "prometheus_url" {
  description = "Prometheus UI"
  value       = "http://${module.compute.public_ip}:9090"
}

output "grafana_url" {
  description = "Grafana UI"
  value       = "http://${module.compute.public_ip}:3000"
}

output "alertmanager_url" {
  description = "Alertmanager UI"
  value       = "http://${module.compute.public_ip}:9093"
}

output "pushgateway_url" {
  description = "Prometheus Pushgateway endpoint"
  value       = "http://${module.compute.public_ip}:9091"
}

output "object_storage_namespace" {
  description = "OCI Object Storage namespace — needed for the S3-compat endpoint"
  value       = data.oci_objectstorage_namespace.this.namespace
}

output "s3_endpoint" {
  description = "S3-compatible endpoint for the lakehouse (set as MINIO_ENDPOINT in pipeline env)"
  value       = "https://${data.oci_objectstorage_namespace.this.namespace}.compat.objectstorage.${var.region}.oraclecloud.com"
}
