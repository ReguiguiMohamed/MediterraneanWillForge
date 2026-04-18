# Mediterranean Ops Fortress — Root Terraform configuration
# Provisions OCI Always-Free infrastructure: ARM VM + Object Storage lakehouse.

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

data "oci_objectstorage_namespace" "this" {
  compartment_id = var.compartment_id
}

# ── Networking (VCN, subnet, internet gateway, security list) ─────────────────
module "networking" {
  source         = "./modules/networking"
  project_name   = var.project_name
  environment    = var.environment
  compartment_id = var.compartment_id
}

# ── Object Storage (Bronze / Silver / Gold lakehouse buckets) ─────────────────
module "storage" {
  source            = "./modules/storage"
  project_name      = var.project_name
  environment       = var.environment
  compartment_id    = var.compartment_id
  namespace         = data.oci_objectstorage_namespace.this.namespace
  lakehouse_buckets = var.lakehouse_buckets
}

# ── Compute (ARM Ampere A1 — always-free 4 OCPUs / 24 GB) ────────────────────
module "compute" {
  source              = "./modules/compute"
  project_name        = var.project_name
  environment         = var.environment
  compartment_id      = var.compartment_id
  subnet_id           = module.networking.subnet_id
  ssh_authorized_keys = var.ssh_authorized_keys
  instance_ocpus      = var.instance_ocpus
  instance_memory_gbs = var.instance_memory_gbs
}
