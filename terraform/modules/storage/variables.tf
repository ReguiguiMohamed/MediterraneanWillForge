variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "network_id" {
  type = string
}

variable "minio_access_key" {
  type      = string
  sensitive = true
}

variable "minio_secret_key" {
  type      = string
  sensitive = true
}

variable "lakehouse_buckets" {
  type = object({
    bronze = string
    silver = string
    gold   = string
  })
}
