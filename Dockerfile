# Production image for ChangSupport.AI (used by Render, see render.yaml).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Download Chroma's all-MiniLM-L6-v2 ONNX model into the image, so the first
# request doesn't wait on it and the app never falls back to hash embeddings.
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as F; F()(['warmup'])"

COPY . .

# The SQLite DB and Chroma store are created and seeded on startup (data/ is
# not baked in), so every deploy or restart begins from the clean demo seed.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
