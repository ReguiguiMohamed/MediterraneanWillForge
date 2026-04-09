output "fortress_network_id" {
  description = "ID of the shared Docker bridge network"
  value       = docker_network.fortress.id
}

output "fortress_network_name" {
  description = "Name of the shared Docker bridge network"
  value       = docker_network.fortress.name
}
