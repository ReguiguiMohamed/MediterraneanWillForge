#!/usr/bin/env bash
# bootstrap.sh — minimal guest provisioning before Ansible takes over.
# Installs: Python 3, pip, Ansible, and essential system packages.
# Everything else is delegated to ansible/site.yml.

set -euo pipefail

echo "[bootstrap] Starting Mediterranean Ops Fortress bootstrap..."

export DEBIAN_FRONTEND=noninteractive

# ── System update ─────────────────────────────────────────────────────────────
apt-get update -qq
apt-get upgrade -y -qq

# ── Core dependencies ──────────────────────────────────────────────────────────
apt-get install -y -qq \
  curl \
  wget \
  git \
  unzip \
  python3 \
  python3-pip \
  python3-venv \
  software-properties-common \
  apt-transport-https \
  ca-certificates \
  gnupg \
  lsb-release

# ── Ansible ────────────────────────────────────────────────────────────────────
pip3 install --quiet --upgrade pip
pip3 install --quiet ansible

echo "[bootstrap] Ansible version: $(ansible --version | head -1)"
echo "[bootstrap] Bootstrap complete. Handing off to Ansible."
