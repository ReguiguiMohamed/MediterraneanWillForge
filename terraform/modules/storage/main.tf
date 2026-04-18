# Storage module — OCI Object Storage buckets for the medallion lakehouse.
# S3-compatible endpoint: https://<namespace>.compat.objectstorage.<region>.oraclecloud.com
# Authentication uses OCI Customer Secret Keys (separate from API signing keys).

resource "oci_objectstorage_bucket" "bronze" {
  compartment_id = var.compartment_id
  namespace      = var.namespace
  name           = "${var.project_name}-${var.lakehouse_buckets.bronze}"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"

  freeform_tags = {
    project = var.project_name
    layer   = "bronze"
  }
}

resource "oci_objectstorage_bucket" "silver" {
  compartment_id = var.compartment_id
  namespace      = var.namespace
  name           = "${var.project_name}-${var.lakehouse_buckets.silver}"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"

  freeform_tags = {
    project = var.project_name
    layer   = "silver"
  }
}

resource "oci_objectstorage_bucket" "gold" {
  compartment_id = var.compartment_id
  namespace      = var.namespace
  name           = "${var.project_name}-${var.lakehouse_buckets.gold}"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"

  freeform_tags = {
    project = var.project_name
    layer   = "gold"
  }
}
