# Compute module — Observability stack: Prometheus, Pushgateway, Grafana, Alertmanager
# Every metric is captured. Every anomaly is surfaced. No blind spots.

locals {
  prometheus_config_path = "${path.root}/../../monitoring/prometheus"
  grafana_config_path    = "${path.root}/../../monitoring/grafana"
  alertmanager_config_path = "${path.root}/../../monitoring/alertmanager"
}

resource "docker_volume" "prometheus_data" {
  name = "${var.project_name}-${var.environment}-prometheus-data"
}

resource "docker_volume" "grafana_data" {
  name = "${var.project_name}-${var.environment}-grafana-data"
}

# ── Prometheus ────────────────────────────────────────────────────────────────
resource "docker_container" "prometheus" {
  name    = "${var.project_name}-prometheus"
  image   = "prom/prometheus:v2.51.0"
  restart = "unless-stopped"

  command = [
    "--config.file=/etc/prometheus/prometheus.yml",
    "--storage.tsdb.path=/prometheus",
    "--storage.tsdb.retention.time=${var.prometheus_retention_days}d",
    "--web.enable-lifecycle",
    "--web.enable-admin-api",
  ]

  ports {
    internal = 9090
    external = 9090
  }

  volumes {
    host_path      = "${local.prometheus_config_path}/prometheus.yml"
    container_path = "/etc/prometheus/prometheus.yml"
    read_only      = true
  }
  volumes {
    host_path      = "${local.prometheus_config_path}/rules"
    container_path = "/etc/prometheus/rules"
    read_only      = true
  }
  volumes {
    volume_name    = docker_volume.prometheus_data.name
    container_path = "/prometheus"
  }

  networks_advanced { name = var.network_id }
  labels { label = "project"; value = var.project_name }
}

# ── Pushgateway ───────────────────────────────────────────────────────────────
resource "docker_container" "pushgateway" {
  name    = "${var.project_name}-pushgateway"
  image   = "prom/pushgateway:v1.8.0"
  restart = "unless-stopped"

  ports {
    internal = 9091
    external = 9091
  }

  networks_advanced { name = var.network_id }
}

# ── Alertmanager ──────────────────────────────────────────────────────────────
resource "docker_container" "alertmanager" {
  name    = "${var.project_name}-alertmanager"
  image   = "prom/alertmanager:v0.27.0"
  restart = "unless-stopped"

  command = [
    "--config.file=/etc/alertmanager/alertmanager.yml",
    "--storage.path=/alertmanager",
  ]

  ports {
    internal = 9093
    external = 9093
  }

  volumes {
    host_path      = "${local.alertmanager_config_path}/alertmanager.yml"
    container_path = "/etc/alertmanager/alertmanager.yml"
    read_only      = true
  }

  networks_advanced { name = var.network_id }
}

# ── Grafana ───────────────────────────────────────────────────────────────────
resource "docker_container" "grafana" {
  name    = "${var.project_name}-grafana"
  image   = "grafana/grafana-oss:10.4.2"
  restart = "unless-stopped"

  env = [
    "GF_SECURITY_ADMIN_USER=admin",
    "GF_SECURITY_ADMIN_PASSWORD=${var.grafana_admin_password}",
    "GF_USERS_ALLOW_SIGN_UP=false",
    "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/pipeline_health.json",
  ]

  ports {
    internal = 3000
    external = 3000
  }

  volumes {
    host_path      = "${local.grafana_config_path}/provisioning"
    container_path = "/etc/grafana/provisioning"
    read_only      = true
  }
  volumes {
    host_path      = "${local.grafana_config_path}/dashboards"
    container_path = "/var/lib/grafana/dashboards"
    read_only      = true
  }
  volumes {
    volume_name    = docker_volume.grafana_data.name
    container_path = "/var/lib/grafana"
  }

  networks_advanced { name = var.network_id }
  labels { label = "project"; value = var.project_name }
}
