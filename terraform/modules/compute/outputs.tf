output "public_ip" {
  description = "Public IP of the fortress VM"
  value       = oci_core_instance.fortress.public_ip
}

output "instance_id" {
  description = "OCID of the fortress instance"
  value       = oci_core_instance.fortress.id
}
