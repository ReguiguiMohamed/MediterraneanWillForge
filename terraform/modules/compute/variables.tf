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

variable "subnet_id" {
  description = "OCID of the public subnet to attach the instance to"
  type        = string
}

variable "ssh_authorized_keys" {
  description = "Public SSH key(s) in authorized_keys format"
  type        = string
}

variable "instance_ocpus" {
  description = "OCPUs for the A1.Flex instance"
  type        = number
  default     = 4
}

variable "instance_memory_gbs" {
  description = "RAM in GB for the A1.Flex instance"
  type        = number
  default     = 24
}
