# The demo GKE env the Mission Runtime's infra operator drives (terraform plan/apply/destroy).
# Deliberately small + cheap: one zonal cluster, one small node pool, spot-capable, demo-labelled + TTL.
# GKE analog of the AWS EKS env — same shape, GCP primitives.
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# The google provider requires a project. `region` is used for regional resources
# (subnet, Artifact Registry); the GKE cluster itself is pinned to a single `zone`
# (zonal, not regional) to keep the demo cheap — one control-plane replica, no cross-AZ nodes.
provider "google" {
  project = var.gcp_project
  region  = var.region
}
