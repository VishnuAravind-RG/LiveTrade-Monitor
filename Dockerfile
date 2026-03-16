FROM python:3.11-slim-bullseye

WORKDIR /app

# libgomp1 is required by tensorflow for OpenMP threading.
# Without it, tensorflow imports silently crash on slim images.
# This is the single most common cause of "works locally, 503 on Railway".
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Upgrade pip first — old pip on slim images sometimes picks wrong wheels
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# Railway injects $PORT at runtime. --timeout 120 prevents gunicorn killing
# workers during slow TF model compilation on first /api/predict request.
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 120 --workers 1 --log-level info app:app