locals {
  name = "${var.project_name}-${var.environment}"

  # GCP uses `labels` where AWS uses default_tags. Mirror the AWS demo tag set.
  # Label values must match ^[a-z0-9_-]{0,63}$ — lowercase the ttl ("24h" is already fine).
  labels = {
    demo    = "gcp"
    project = var.project_name
    env     = var.environment
    ttl     = var.ttl
  }
}

# ---- VPC (custom, no auto subnets — keeps the demo footprint explicit + cheap) ----
resource "google_compute_network" "vpc" {
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.name}-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.vpc.id

  # Secondary ranges make the GKE cluster VPC-native (alias IPs) — the GKE default + best practice.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }

  private_ip_google_access = true
}

# ---- GKE (zonal, one small node pool) ----
# Zonal (location = single zone) not regional: one control-plane replica, no cross-AZ node spread —
# the cheap-demo analog of the AWS single-NAT / one-managed-node-group choice.
resource "google_container_cluster" "gke" {
  name     = local.name
  location = var.zone

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # Best practice: create the cluster with a throwaway default pool, then remove it and attach
  # our own managed node pool below (lets us own the node config / lifecycle).
  remove_default_node_pool = true
  initial_node_count       = 1

  # Demo hygiene: let `terraform destroy` actually delete the cluster.
  deletion_protection = false

  release_channel {
    channel = var.release_channel
  }

  # VPC-native cluster wired to the subnet's secondary ranges.
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Send workload logs/metrics nowhere fancy — default GKE system logging is fine and cheap.
  resource_labels = local.labels
}

resource "google_container_node_pool" "demo" {
  name     = "demo-nodes"
  location = var.zone
  cluster  = google_container_cluster.gke.name

  node_count = var.node_desired_size

  autoscaling {
    min_node_count = var.node_min_size
    max_node_count = var.node_max_size
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type = var.node_machine_type
    disk_size_gb = var.node_disk_size
    disk_type    = "pd-standard" # cheapest; nothing in the demo needs SSD throughput

    # Spot VMs — the GCP analog of AWS SPOT capacity_type. Cheaper, fine for a demo.
    spot = var.use_spot

    # cloud-platform scope so the nodes can pull from Artifact Registry etc. (demo-simple).
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    labels          = { role = "demo" }
    resource_labels = local.labels

    # Spot nodes carry the standard preemption-safe posture; no local SSDs, no GPUs.
  }
}

# ---- Artifact Registry Docker repos (the ECR analog) ----
# One Docker repo per demo workload via for_each over the same name list the AWS env used for ECR.
# Vulnerability scanning is enabled at the project level (Container/Artifact Analysis API), not per
# repo, so there's no per-repo scan_on_push flag here — the AR repo is just the registry surface.
resource "google_artifact_registry_repository" "repo" {
  for_each = toset(var.artifact_repos)

  location      = var.region
  repository_id = "${local.name}-${each.value}"
  format        = "DOCKER"
  description   = "Demo image repo for ${each.value} (edge-sentinel scans these)."
  labels        = local.labels
}
