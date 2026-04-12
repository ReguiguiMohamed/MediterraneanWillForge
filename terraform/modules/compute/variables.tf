variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "network_id" {
  type = string
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
}

variable "prometheus_retention_days" {
  type    = number
  default = 30
}
