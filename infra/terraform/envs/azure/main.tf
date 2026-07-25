locals {
  name = "${var.project_name}-${var.environment}"

  # demo tags — the azurerm analog of the AWS provider's default_tags block
  tags = {
    demo    = "azure"
    project = var.project_name
    env     = var.environment
    ttl     = var.ttl
  }

  # ACR names are globally unique and alphanumeric-only (no hyphens); derive one and add a short
  # random suffix so `terraform apply` doesn't collide with an existing registry name.
  acr_name = "${lower(replace(local.name, "-", ""))}${random_string.acr_suffix.result}"
}

resource "random_string" "acr_suffix" {
  length  = 6
  upper   = false
  special = false
}

# ---- Resource group (everything lives here; TTL-tagged for the teardown mission) ----
resource "azurerm_resource_group" "demo" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

# ---- VNet + subnet (single subnet keeps the demo cheap; azure CNI pods share it) ----
resource "azurerm_virtual_network" "demo" {
  name                = "${local.name}-vnet"
  location            = azurerm_resource_group.demo.location
  resource_group_name = azurerm_resource_group.demo.name
  address_space       = [var.vnet_cidr]
  tags                = local.tags
}

resource "azurerm_subnet" "nodes" {
  name                 = "${local.name}-nodes"
  resource_group_name  = azurerm_resource_group.demo.name
  virtual_network_name = azurerm_virtual_network.demo.name
  address_prefixes     = [var.node_subnet_cidr]
}

# ---- AKS (Free-tier control plane, one small system node pool, system-assigned managed identity) ----
resource "azurerm_kubernetes_cluster" "demo" {
  name                = local.name
  location            = azurerm_resource_group.demo.location
  resource_group_name = azurerm_resource_group.demo.name
  dns_prefix          = local.name
  kubernetes_version  = var.kubernetes_version

  # Free tier = no control-plane charge + no uptime SLA — right for a $0-plan demo.
  sku_tier = "Free"

  # system-assigned managed identity == the AWS demo's role-scoped cluster identity (no static keys)
  identity {
    type = "SystemAssigned"
  }

  # one small system node pool: 2/3/2 autoscale, ~30GB, matching the AWS managed node group.
  # NOTE: Azure requires the default/system pool to be Regular priority — Spot lives on a user pool
  # (see azurerm_kubernetes_cluster_node_pool.spot, gated by var.use_spot).
  default_node_pool {
    name                        = "system"
    vm_size                     = var.node_vm_size
    os_disk_size_gb             = var.node_disk_size
    auto_scaling_enabled        = true
    min_count                   = var.node_min_size
    max_count                   = var.node_max_size
    node_count                  = var.node_desired_size
    vnet_subnet_id              = azurerm_subnet.nodes.id
    temporary_name_for_rotation = "systemtmp"
    node_labels                 = { role = "demo" }
    tags                        = local.tags
  }

  network_profile {
    network_plugin = "azure"
    service_cidr   = "10.43.0.0/16"
    dns_service_ip = "10.43.0.10"
  }

  # Empty authorized_ip_ranges == fully public endpoint (the AWS default is 0.0.0.0/0). Azure rejects
  # 0.0.0.0/0 as an authorized range, so we omit the block entirely when the list is empty.
  dynamic "api_server_access_profile" {
    for_each = length(var.authorized_ip_ranges) > 0 ? [1] : []
    content {
      authorized_ip_ranges = var.authorized_ip_ranges
    }
  }

  tags = local.tags
}

# ---- Optional Spot user node pool (scale-to-zero when idle → cheap) for demo workloads ----
# The AWS env runs its single node group on Spot; on Azure Spot can only be a *user* pool, so
# use_spot adds this alongside the Regular system pool. min_count = 0 keeps it ~$0 while idle.
resource "azurerm_kubernetes_cluster_node_pool" "spot" {
  count = var.use_spot ? 1 : 0

  name                  = "spot"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.demo.id
  vm_size               = var.node_vm_size
  os_disk_size_gb       = var.node_disk_size

  priority        = "Spot"
  eviction_policy = "Delete"
  spot_max_price  = -1 # -1 == pay up to the on-demand price (never evicted on price)

  auto_scaling_enabled = true
  min_count            = 0
  max_count            = var.node_max_size
  node_count           = 0

  vnet_subnet_id = azurerm_subnet.nodes.id

  # taint so only workloads that tolerate Spot land here; the Azure scheduler adds the spot label too
  node_labels = { role = "demo-spot" }
  node_taints = ["kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
  tags        = local.tags
}

# ---- ACR (Basic SKU; repos are created on first push — see var.acr_repos for the concept) ----
resource "azurerm_container_registry" "demo" {
  name                = local.acr_name
  resource_group_name = azurerm_resource_group.demo.name
  location            = azurerm_resource_group.demo.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

# Let the cluster's kubelet identity pull from ACR (the AKS<->ACR attach, minus the `az aks` helper).
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                            = azurerm_container_registry.demo.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.demo.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}
