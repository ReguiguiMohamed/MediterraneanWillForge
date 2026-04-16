# Storage module — MinIO containers and Docker volumes
# This is the lakehouse backend. Every layer of the medallion lives here.

resource "docker_volume" "minio_data" {
  name = "${var.project_name}-${var.environment}-minio-data"
  labels {
    label = "project"
    value = var.project_name
  }
}

resource "docker_container" "minio" {
  name  = "${var.project_name}-minio"
  image = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"

  restart = "unless-stopped"

  command = ["server", "/data", "--console-address", ":9001"]

  env = [
    "MINIO_ROOT_USER=${var.minio_access_key}",
    "MINIO_ROOT_PASSWORD=${var.minio_secret_key}",
    "MINIO_PROMETHEUS_AUTH_TYPE=public",
  ]

  ports {
    internal = 9000
    external = 9000
  }
  ports {
    internal = 9001
    external = 9001
  }

  volumes {
    volume_name    = docker_volume.minio_data.name
    container_path = "/data"
  }

  networks_advanced {
    name = var.network_id
  }

  healthcheck {
    test         = ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
    interval     = "30s"
    timeout      = "10s"
    retries      = 3
    start_period = "10s"
  }

  labels {
    label = "project"
    value = var.project_name
  }
}

# Create bucket directories via MinIO mc client after container is up
resource "null_resource" "create_buckets" {
  depends_on = [docker_container.minio]

  provisioner "local-exec" {
    command = <<-EOT
      sleep 5
      docker run --rm --network ${var.network_id} \
        --entrypoint /bin/sh \
        minio/mc:RELEASE.2025-08-13T08-35-41Z -c "
          mc alias set local http://${docker_container.minio.name}:9000 \
            ${var.minio_access_key} ${var.minio_secret_key} && \
          mc mb --ignore-existing local/${var.lakehouse_buckets.bronze} && \
          mc mb --ignore-existing local/${var.lakehouse_buckets.silver} && \
          mc mb --ignore-existing local/${var.lakehouse_buckets.gold}
        "
    EOT
  }
}
