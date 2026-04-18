variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "compartment_id" {
  description = "OCI compartment OCID"
  type        = string
}

variable "namespace" {
  description = "OCI Object Storage namespace (from data.oci_objectstorage_namespace)"
  type        = string
}

variable "lakehouse_buckets" {
  description = "Bucket name suffixes for each medallion layer"
  type = object({
    bronze = string
    silver = string
    gold   = string
  })
}
