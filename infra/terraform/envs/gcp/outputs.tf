output "cluster_name" {
  value = google_container_cluster.gke.name
}

output "cluster_endpoint" {
  value = google_container_cluster.gke.endpoint
}

output "region" {
  value = var.region
}

output "zone" {
  value = var.zone
}

output "kubeconfig_command" {
  description = "Point kubectl/helm at the demo cluster (GKE analog of `aws eks update-kubeconfig`)."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.gke.name} --zone ${var.zone} --project ${var.gcp_project}"
}

# Registry host for the region (the ECR-registry analog).
output "artifact_registry_host" {
  value = "${var.region}-docker.pkg.dev"
}

# Fully-qualified push/pull URL per demo workload:  REGION-docker.pkg.dev/PROJECT/REPO_ID
output "artifact_registry_repo_urls" {
  value = {
    for k, r in google_artifact_registry_repository.repo :
    k => "${var.region}-docker.pkg.dev/${var.gcp_project}/${r.repository_id}"
  }
}
