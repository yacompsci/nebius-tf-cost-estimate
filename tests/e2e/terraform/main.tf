# Minimal create-only stack that produces real `terraform show -json` plan
# output for nebius/nebius without live parent/network IDs.
#
# Placeholder IDs below are intentionally non-live. With
# `no_credentials` + `disable_preflight_checks` the provider still emits a
# plan-valid resource_changes payload that nebius-tf-cost can estimate.

locals {
  parent_id = "project-e2e0000000000000000000000"
}

resource "nebius_compute_v1_disk" "data" {
  parent_id      = local.parent_id
  name           = "e2e-data"
  type           = "NETWORK_SSD"
  size_gibibytes = 100
}

resource "nebius_compute_v1_instance" "training" {
  parent_id = local.parent_id
  name      = "e2e-training"

  resources = {
    platform = "cpu-e2"
    preset   = "2vcpu-8gb"
  }

  boot_disk = {
    attach_mode = "READ_WRITE"
    managed_disk = {
      name = "e2e-training-boot"
      spec = {
        type           = "NETWORK_SSD"
        size_gibibytes = 64
        source_image_family = {
          image_family = "ubuntu22.04"
        }
      }
    }
  }

  network_interfaces = [
    {
      name      = "eth0"
      subnet_id = "vpcsubnet-e2e0000000000000000000000"
      ip_address = {
        allocation_id = "vpcallocation-e2e000000000000000000"
      }
    }
  ]
}

resource "nebius_storage_v1_bucket" "datasets" {
  parent_id             = local.parent_id
  name                  = "e2e-datasets"
  default_storage_class = "STANDARD"
}

resource "nebius_vpc_v1_network" "main" {
  parent_id = local.parent_id
  name      = "e2e-main"
}
