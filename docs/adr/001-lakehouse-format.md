# ADR-001: Delta Lake as the Lakehouse Table Format

**Status:** Accepted
**Date:** 2024-04-09
**Last reviewed:** 2026-05-28

## Context

The platform requires a table format that supports ACID transactions, schema
evolution, partition pruning, and time travel on S3-compatible object storage.
The original local target was MinIO; the hosted automation target is now
Backblaze B2, with MinIO retained for local development and CI. Two options were
evaluated: **Delta Lake** and **Apache Iceberg**.

## Decision

We use **Delta Lake** via the `deltalake` Python library (pure Rust
implementation, no JVM dependency).

## Rationale

| Criterion | Delta Lake | Iceberg |
|---|---|---|
| Python-native, no JVM | Yes, `deltalake` Rust bindings | Requires PyIceberg plus a Java-backed catalog in common deployments |
| Schema evolution | `schema_mode="merge"` built in | Supported, but more catalog/config work |
| Time travel | Versioned Delta log support | Supported |
| S3-compatible storage | Works with Backblaze B2 and MinIO | Works, but still needs catalog decisions |
| Operational complexity | Low, no catalog server required | Higher, commonly needs Hive/REST/catalog infrastructure |

For a local-first, JVM-free stack, Delta Lake with `deltalake` requires no table
catalog service beyond object storage. Iceberg's catalog requirement would add
operational weight with no measurable benefit at this data volume.

## Consequences

- All medallion layers use Delta Lake format.
- Schema evolution is handled via `schema_mode="merge"` on writes where needed.
- Writes attempt `create_checkpoint()` after `write_deltalake()` so B2 Class B
  reads stay bounded when tables are opened later.
- If data volumes grow beyond this project's portfolio scale, migration to a
  managed catalog or an additional table format remains possible without
  changing the high-level pipeline boundaries.
