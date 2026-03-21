FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY templates/ templates/

# Create upload dir
RUN mkdir -p /tmp/mi_uploads

EXPOSE 8080

CMD ["python", "-m", "src.server"]
