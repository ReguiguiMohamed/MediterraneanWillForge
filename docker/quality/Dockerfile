# MediterraneanWillForge - quality job image
# Build context: repository root (not docker/quality/).
# docker build -f docker/quality/Dockerfile .

FROM python:3.11.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Keep the real package layout so data.storage imports work.
COPY data/ ./data/

RUN useradd -m -u 1001 medops && chown -R medops:medops /app
USER medops

CMD ["python", "-m", "data.quality.run_checks"]
