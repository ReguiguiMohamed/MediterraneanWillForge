# Mediterranean Ops Fortress — Ingestion Job Image
# Build context: repository root (not docker/ingestion/).
# docker build -f docker/ingestion/Dockerfile .

FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/ingestion/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application source — paths relative to repo root (build context)
COPY data/ingestion/ ./ingestion/
COPY data/schemas/   ./schemas/

RUN useradd -m -u 1001 medops && chown -R medops:medops /app
USER medops

# Default: run the Open-Meteo Air Quality ingestor.
# Override at runtime: python -m ingestion.bronze.openaq_ingestor
CMD ["python", "-m", "ingestion.bronze.copernicus_ingestor"]
