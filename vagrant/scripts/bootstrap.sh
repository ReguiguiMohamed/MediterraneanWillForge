#!/usr/bin/env bash
# bootstrap.sh — minimal guest provisioning before Ansible takes over.
# Installs: Python 3, pip, Ansible, and required Galaxy collections.
# Vagrant's ansible_local provisioner is told NOT to re-install Ansible
# (ansible.install = false in the Vagrantfile), so this script owns it.

set -euo pipefail

echo "[bootstrap] Starting Mediterranean Ops Fortress bootstrap..."

export DEBIAN_FRONTEND=noninteractive

# ── System update ──────────────────────────────────────────────────────────────
apt-get update -qq
apt-get upgrade -y -qq

# ── Core dependencies ─────────────────────────────────────────────────────────
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

# ── Ansible ───────────────────────────────────────────────────────────────────
pip3 install --quiet --upgrade pip
pip3 install --quiet ansible

echo "[bootstrap] Ansible version: $(ansible --version | head -1)"

# ── Ansible Galaxy collections ────────────────────────────────────────────────
# Synced folder is already mounted at /opt/med-ops-fortress before provisioners run.
REQUIREMENTS_FILE="/opt/med-ops-fortress/ansible/requirements.yml"

if [ -f "$REQUIREMENTS_FILE" ]; then
  echo "[bootstrap] Installing Ansible Galaxy collections from $REQUIREMENTS_FILE..."
  ansible-galaxy collection install -r "$REQUIREMENTS_FILE" --force
else
  echo "[bootstrap] requirements.yml not found — installing collections directly..."
  ansible-galaxy collection install community.general community.docker --force
fi

echo "[bootstrap] Bootstrap complete. Handing off to Ansible."
