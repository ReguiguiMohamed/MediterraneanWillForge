output "minio_api_port" {
  value = 9000
}

output "minio_console_port" {
  value = 9001
}

output "minio_container_name" {
  value = docker_container.minio.name
}
