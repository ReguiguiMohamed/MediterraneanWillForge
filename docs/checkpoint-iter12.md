# Checkpoint — Iter 12 (v1.1.0)

**Date:** 2026-04-18
**Commit:** c772500
**Tag:** v1.1.0

## What Was Done

### Infrastructure switch (local → cloud-ready)

| Component | Before | After |
|---|---|---|
| Terraform provider | `kreuzwerker/docker` (local Docker) | `oracle/oci ~> 6.0` (OCI ARM A1 VM) |
| Object storage | MinIO in Docker Compose | Backblaze B2 (S3-compat, eu-central-003) |
| Ansible roles | 6 (incl. minio) | 5 (minio role deleted) |
| Alertmanager receivers | `localhost:5001` stubs | Slack `slack_configs` (Ansible path) |
| Docker Compose | MinIO in base stack | MinIO in `docker-compose.override.yml` only |

### Files changed
- `terraform/` — full rewrite of all modules and versions files for OCI
- `terraform/environments/local/` — deleted; replaced with `environments/cloud/terraform.tfvars.example`
- `terraform/modules/compute/cloud-init.yaml` — new: lightweight VM bootstrap for OCI
- `ansible/site.yml` — minio role removed
- `ansible/roles/minio/` — deleted
- `ansible/inventory/hosts.ini` — now targets remote OCI VM (ubuntu user, SSH key)
- `ansible/vars/common.yml` — B2 storage vars, Slack webhook vars, minio vars removed
- `ansible/roles/alertmanager/templates/alertmanager.yml.j2` — `slack_configs` routing
- `docker/docker-compose.yml` — MinIO service removed; `MINIO_ENDPOINT` from env
- `docker/docker-compose.override.yml` — new: local dev MinIO overlay
- `data/dbt/profiles.yml` — `s3_use_ssl` now driven by `S3_USE_SSL` env var
- `.env.example` — updated for B2 endpoint, bucket names, `MINIO_HOST`, `S3_USE_SSL`
- `monitoring/alertmanager/alertmanager.yml` — comments updated; stubs kept for local dev
- `README.md` — stack table, architecture, quick start, prerequisites, limitations updated

## Current State

- CI: all 3 workflows green (ci-data, ci-infra, cd-deploy)
- B2 buckets: created, credentials configured in `.env`, **pipeline not yet run against B2**
- OCI VM: Terraform written, not applied (no credit card available)
- Slack: vars in Ansible template, webhook URLs not yet created

## Known Blocker — Next Session

**delta-rs cannot connect to B2.** Error observed when running ingestion:

```
thread panicked: request valid: reqwest::Error { kind: Builder, source: RelativeUrlWithoutBase }
```

**Root cause:** `data/ingestion/bronze/base.py` `StorageConfig.options` uses wrong key names:

```python
# Current (broken for cloud S3-compat backends)
"endpoint_url": self.endpoint        # should be AWS_ENDPOINT_URL
# Missing entirely:
# "AWS_REGION": "eu-central-003"
```

**Fix required in next session:**
1. `data/ingestion/bronze/base.py` — rename keys to uppercase, add `AWS_REGION`
2. `data/ingestion/silver/transformer.py` — same storage_options audit
3. `data/ingestion/gold/marts.py` — same storage_options audit
4. Add `AWS_REGION=eu-central-003` to `.env.example`
5. Run the pipeline end-to-end against B2 to confirm all three buckets fill
