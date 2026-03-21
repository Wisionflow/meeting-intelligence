FROM python:3.12-slim

WORKDIR /app

# System deps for faster-whisper (CTranslate2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY templates/ templates/

# Create dirs
RUN mkdir -p /tmp/mi_uploads

EXPOSE 8080

CMD ["python", "-m", "src.server"]
