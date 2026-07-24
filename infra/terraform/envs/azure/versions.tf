# The demo AKS env the Mission Runtime's infra operator drives (terraform plan/apply/destroy).
# Azure analog of envs/aws. Deliberately small + cheap: one small system node pool, Free-tier AKS
# control plane, Basic ACR, demo-tagged + TTL. Mirrors the AWS env's shape and $0-plan spirit.
terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  # azurerm has no provider-level default_tags (unlike aws); demo tags are applied per-resource
  # via local.tags below. subscription_id comes from ARM_SUBSCRIPTION_ID unless set explicitly.
  subscription_id = var.subscription_id != "" ? var.subscription_id : null
  features {}
}
