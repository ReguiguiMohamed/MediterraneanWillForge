# Mediterranean Ops Fortress — Ingestion Job Image
# Lean, purpose-built. Pulls climate data from Copernicus and EEA,
# writes raw records into the Bronze Delta Lake layer on MinIO.

FROM python:3.11-slim AS base

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────────────────
COPY ../../data/ingestion ./ingestion
COPY ../../data/schemas    ./schemas

# ── Non-root user ──────────────────────────────────────────────────────────────
RUN useradd -m -u 1001 medops && chown -R medops:medops /app
USER medops

# ── Entrypoint ─────────────────────────────────────────────────────────────────
# Override CMD at runtime: e.g., python -m ingestion.bronze.copernicus_ingestor
CMD ["python", "-m", "ingestion.bronze.copernicus_ingestor"]
