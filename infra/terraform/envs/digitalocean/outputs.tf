output "cluster_name" {
  value = digitalocean_kubernetes_cluster.demo.name
}

output "cluster_endpoint" {
  value = digitalocean_kubernetes_cluster.demo.endpoint
}

output "region" {
  value = var.region
}

# DOKS also exposes a ready-to-use kubeconfig directly (digitalocean_kubernetes_cluster.demo.kube_config,
# sensitive) — this command is the doctl equivalent of `aws eks update-kubeconfig`.
output "kubeconfig_command" {
  description = "Point kubectl/helm at the demo cluster."
  value       = "doctl kubernetes cluster kubeconfig save ${digitalocean_kubernetes_cluster.demo.name}"
}

# registry.digitalocean.com/<registry-name> — the single account-global DOCR endpoint.
output "docr_registry" {
  value = digitalocean_container_registry.demo.endpoint
}

# app-name -> full image repo path inside the single DOCR (DO analog of ecr_repo_urls).
output "app_image_repos" {
  value = { for a in var.app_repos : a => "${digitalocean_container_registry.demo.endpoint}/${a}" }
}
