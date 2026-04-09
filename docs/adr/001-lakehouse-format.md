# ADR-001: Delta Lake as the Lakehouse Table Format

**Status:** Accepted  
**Date:** 2024-04-09

## Context

The platform requires a table format that supports: ACID transactions, schema evolution, partition pruning, and time travel — all on top of MinIO (S3-compatible) object storage. Two options were evaluated: **Delta Lake** and **Apache Iceberg**.

## Decision

We use **Delta Lake** via the `deltalake` Python library (pure Rust implementation, no JVM dependency).

## Rationale

| Criterion                | Delta Lake                         | Iceberg                            |
|--------------------------|------------------------------------|------------------------------------|
| Python-native (no JVM)   | Yes — `deltalake` Rust bindings    | Requires PyIceberg + Java catalog  |
| Schema evolution         | `schema_mode="merge"` built-in     | Supported, but more config         |
| Time travel              | `as_of_version()` built-in         | Supported                          |
| MinIO compatibility      | S3-compatible, works out of the box| Works, but catalog overhead        |
| Ecosystem maturity       | Strong (Databricks origin)         | Strong (Netflix/Apple origin)      |
| Operational complexity   | Low — no catalog server needed     | Higher — Hive/REST catalog needed  |

For a local-first, JVM-free stack, Delta Lake with `deltalake` requires zero infrastructure beyond MinIO. Iceberg's catalog requirement would add operational weight with no measurable benefit at this data volume.

## Consequences

- All three medallion layers use Delta Lake format.
- Schema evolution is handled via `schema_mode="merge"` on write, logged as Prometheus metrics when detected.
- If data volumes grow beyond local capacity, migration to a managed catalog (Unity Catalog, Polaris) is straightforward — Iceberg support can be added as a second format without changing the pipeline interface.
