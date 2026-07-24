output "cluster_name" {
  value = azurerm_kubernetes_cluster.demo.name
}

output "cluster_endpoint" {
  description = "AKS API server FQDN (the Azure analog of the EKS cluster endpoint)."
  value       = azurerm_kubernetes_cluster.demo.fqdn
}

output "region" {
  description = "Alias of location, kept for parity with the AWS env's `region` output."
  value       = var.location
}

output "location" {
  value = var.location
}

output "resource_group" {
  value = azurerm_resource_group.demo.name
}

output "kubeconfig_command" {
  description = "Point kubectl/helm at the demo cluster (the `aws eks update-kubeconfig` equivalent)."
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.demo.name} --name ${azurerm_kubernetes_cluster.demo.name}"
}

output "acr_registry" {
  description = "ACR login server; push demo images here (edge-sentinel scans them)."
  value       = azurerm_container_registry.demo.login_server
}

# ACR creates repos on first push, so there are no per-repo resources; surface the intended push
# targets (login_server/<repo>) the same way the AWS env lists ecr_repo_urls.
output "acr_repo_urls" {
  value = { for r in var.acr_repos : r => "${azurerm_container_registry.demo.login_server}/${r}" }
}
