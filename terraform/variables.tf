# ── OCI authentication ────────────────────────────────────────────────────────
variable "tenancy_ocid" {
  description = "OCID of the OCI tenancy"
  type        = string
}

variable "user_ocid" {
  description = "OCID of the OCI user"
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of the OCI API signing key"
  type        = string
}

variable "private_key_path" {
  description = "Local path to the OCI API private key (.pem)"
  type        = string
}

variable "region" {
  description = "OCI region (e.g. eu-paris-1, us-ashburn-1)"
  type        = string
  default     = "eu-paris-1"
}

variable "compartment_id" {
  description = "OCID of the compartment to deploy into (use tenancy OCID for root)"
  type        = string
}

# ── Project ───────────────────────────────────────────────────────────────────
variable "environment" {
  description = "Deployment environment name"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging"], var.environment)
    error_message = "Environment must be prod or staging."
  }
}

variable "project_name" {
  description = "Project identifier used in resource naming"
  type        = string
  default     = "med-ops-fortress"
}

# ── Compute ───────────────────────────────────────────────────────────────────
variable "ssh_authorized_keys" {
  description = "Public SSH key(s) to install on the VM (authorized_keys format)"
  type        = string
}

variable "instance_ocpus" {
  description = "Number of OCPUs for the Ampere A1 instance (always-free cap: 4)"
  type        = number
  default     = 4
}

variable "instance_memory_gbs" {
  description = "RAM in GB for the Ampere A1 instance (always-free cap: 24)"
  type        = number
  default     = 24
}

# ── Storage ───────────────────────────────────────────────────────────────────
variable "lakehouse_buckets" {
  description = "OCI Object Storage bucket names for each medallion layer"
  type = object({
    bronze = string
    silver = string
    gold   = string
  })
  default = {
    bronze = "bronze"
    silver = "silver"
    gold   = "gold"
  }
}
