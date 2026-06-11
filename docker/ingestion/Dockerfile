# MediterraneanWillForge - pipeline job image
# Build context: repository root (not docker/ingestion/).
# docker build -f docker/ingestion/Dockerfile .

FROM python:3.11.15-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Keep the real package layout so data.storage and data.metrics imports work.
COPY data/ ./data/

RUN useradd -m -u 1001 medops && chown -R medops:medops /app
USER medops

# Default: run the Open-Meteo Air Quality ingestor.
# Override at runtime: python -m data.ingestion.bronze.openaq_ingestor
CMD ["python", "-m", "data.ingestion.bronze.copernicus_ingestor"]
