#!/usr/bin/env bash
# setup.sh — Bootstrap a fresh developer workstation.
# Installs Vagrant, Terraform CLI, Ansible, and Python dependencies.
# Intended for first-time project setup on macOS/Linux.

set -euo pipefail

echo ""
echo "Mediterranean Ops Fortress — Developer Setup"
echo "With discipline and clarity, the environment is forged."
echo ""

# ── Detect OS ──────────────────────────────────────────────────────────────────
OS=$(uname -s)
echo "[1/5] Detected OS: $OS"

# ── Terraform ─────────────────────────────────────────────────────────────────
if ! command -v terraform &>/dev/null; then
  echo "[2/5] Installing Terraform..."
  if [ "$OS" = "Darwin" ]; then
    brew tap hashicorp/tap && brew install hashicorp/tap/terraform
  else
    TERRAFORM_VERSION="1.8.0"
    curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/terraform.zip
    unzip -o /tmp/terraform.zip -d /usr/local/bin/
    rm /tmp/terraform.zip
  fi
else
  echo "[2/5] Terraform already installed: $(terraform version -json | python3 -c 'import sys,json; print(json.load(sys.stdin)["terraform_version"])')"
fi

# ── Ansible ───────────────────────────────────────────────────────────────────
if ! command -v ansible &>/dev/null; then
  echo "[3/5] Installing Ansible via pip..."
  pip3 install --user ansible
else
  echo "[3/5] Ansible already installed: $(ansible --version | head -1)"
fi

# ── Python dependencies ───────────────────────────────────────────────────────
echo "[4/5] Installing Python dependencies..."
pip3 install -r docker/ingestion/requirements.txt
pip3 install ruff black pytest pytest-cov

# ── Copy env file ─────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "[5/5] Copying .env.example → .env"
  cp .env.example .env
  echo "      Edit .env and add your CAMS_API_KEY before running ingestion."
else
  echo "[5/5] .env already exists — skipping."
fi

echo ""
echo "Setup complete. Run 'make up' to start the Vagrant VM."
echo ""
