# Networking module — OCI VCN, public subnet, internet gateway, security list.
# Opens only the ports the observability stack needs. SSH is restricted to key auth.

resource "oci_core_vcn" "fortress" {
  compartment_id = var.compartment_id
  cidr_blocks    = ["10.0.0.0/16"]
  display_name   = "${var.project_name}-${var.environment}-vcn"
  dns_label      = "fortress"

  freeform_tags = {
    project     = var.project_name
    environment = var.environment
  }
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.fortress.id
  display_name   = "${var.project_name}-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.fortress.id
  display_name   = "${var.project_name}-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_security_list" "fortress" {
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.fortress.id
  display_name   = "${var.project_name}-sl"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
    stateless   = false
  }

  # SSH
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 22
      max = 22
    }
  }

  # Prometheus
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 9090
      max = 9090
    }
  }

  # Pushgateway
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 9091
      max = 9091
    }
  }

  # Alertmanager
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 9093
      max = 9093
    }
  }

  # Grafana
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 3000
      max = 3000
    }
  }

  # cAdvisor
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 8080
      max = 8080
    }
  }

  # node_exporter
  ingress_security_rules {
    protocol  = "6"
    source    = "0.0.0.0/0"
    stateless = false
    tcp_options {
      min = 9100
      max = 9100
    }
  }

  freeform_tags = {
    project     = var.project_name
    environment = var.environment
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_id
  vcn_id            = oci_core_vcn.fortress.id
  cidr_block        = "10.0.1.0/24"
  display_name      = "${var.project_name}-public-subnet"
  dns_label         = "pub"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.fortress.id]
}
