output "subnet_id" {
  description = "OCID of the public subnet — passed to compute module"
  value       = oci_core_subnet.public.id
}

output "vcn_id" {
  description = "OCID of the fortress VCN"
  value       = oci_core_vcn.fortress.id
}
