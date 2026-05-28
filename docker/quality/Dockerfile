# Mediterranean Ops Fortress - Data Quality Job Image
# Build context: repository root (not docker/quality/).
# docker build -f docker/quality/Dockerfile .

FROM python:3.11.15-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
      great-expectations==0.18.19 \
      deltalake==0.18.1 \
      pyarrow==16.1.0 \
      boto3==1.34.131 \
      s3fs==2024.6.0 \
      prometheus-client==0.20.0 \
      python-dotenv==1.0.1 \
      loguru==0.7.2

# Keep the real package layout so data.storage imports work.
COPY data/ ./data/

RUN useradd -m -u 1001 medops && chown -R medops:medops /app
USER medops

CMD ["python", "-m", "data.quality.run_checks"]
