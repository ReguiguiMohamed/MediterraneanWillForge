"""
data/metrics.py
───────────────
Prometheus remote_write helper for Grafana Cloud Mimir.

Pushes pipeline metrics to Grafana Cloud after each stage.
Falls back silently to a no-op when the three GRAFANA_* env vars are
not all present — so local dev and CI without Grafana secrets configured
are completely unaffected.

Usage:
    from data.metrics import push_to_grafana
    push_to_grafana(registry, job="med_ops_bronze_openmeteo")

Auth: Basic Auth with GRAFANA_METRICS_ID as username and GRAFANA_TOKEN
as password, matching Grafana Cloud Access Policy token scheme.

Protobuf encoding is done inline (no protobuf package dependency) using
the minimal WriteRequest / TimeSeries / Label / Sample fields required
by the Prometheus remote_write specification.
"""

from __future__ import annotations

import base64
import os
import struct
import time

import requests
import snappy
from loguru import logger
from prometheus_client import CollectorRegistry


def push_to_grafana(registry: CollectorRegistry, job: str) -> None:
    """Push a CollectorRegistry to Grafana Cloud via Prometheus remote_write.

    Silent no-op when GRAFANA_REMOTE_WRITE_URL / GRAFANA_METRICS_ID /
    GRAFANA_TOKEN are not all set. Never raises — logs a warning on failure.
    """
    url = os.environ.get("GRAFANA_REMOTE_WRITE_URL", "").strip()
    uid = os.environ.get("GRAFANA_METRICS_ID", "").strip()
    token = os.environ.get("GRAFANA_TOKEN", "").strip()

    if not (url and uid and token):
        return

    try:
        payload = _build_write_request(registry, extra_labels={"job": job})
        compressed = snappy.compress(payload)
        creds = base64.b64encode(f"{uid}:{token}".encode()).decode()
        resp = requests.post(
            url,
            data=compressed,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-protobuf",
                "Content-Encoding": "snappy",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug(f"Grafana remote_write OK (HTTP {resp.status_code}) — job={job}")
    except Exception as exc:
        logger.warning(f"Grafana remote_write failed (non-fatal) [job={job}]: {exc}")


# ── Minimal Prometheus remote_write protobuf encoder ──────────────────────────
# Implements only WriteRequest > TimeSeries > Label / Sample.
# Wire types used:
#   0 — varint  (int64 timestamp)
#   1 — 64-bit  (double value)
#   2 — length-delimited  (strings, embedded messages)


def _varint(n: int) -> bytes:
    result = []
    while n > 0x7F:
        result.append((n & 0x7F) | 0x80)
        n >>= 7
    result.append(n)
    return bytes(result)


def _ldelim(field: int, data: bytes) -> bytes:
    """Encode a length-delimited protobuf field (wire type 2)."""
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def _encode_label(name: str, value: str) -> bytes:
    return _ldelim(1, name.encode()) + _ldelim(2, value.encode())


def _encode_sample(value: float, ts_ms: int) -> bytes:
    val_part = _varint((1 << 3) | 1) + struct.pack("<d", value)
    ts_part = _varint((2 << 3) | 0) + _varint(ts_ms)
    return val_part + ts_part


def _encode_timeseries(labels: dict[str, str], value: float, ts_ms: int) -> bytes:
    label_parts = b"".join(
        _ldelim(1, _encode_label(k, v)) for k, v in sorted(labels.items())
    )
    return label_parts + _ldelim(2, _encode_sample(value, ts_ms))


def _build_write_request(
    registry: CollectorRegistry, extra_labels: dict[str, str]
) -> bytes:
    ts_ms = int(time.time() * 1000)
    parts = []
    for mf in registry.collect():
        for sample in mf.samples:
            labels = {"__name__": sample.name, **extra_labels, **sample.labels}
            parts.append(
                _ldelim(1, _encode_timeseries(labels, float(sample.value), ts_ms))
            )
    return b"".join(parts)
