# Use official Python image as base
FROM python:3.11-slim

# Set work directory
WORKDIR /app

# Install system dependencies (optional, for common Python packages)
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# routes/webhook.py persiste en data/webhooks.json -- ese directorio se monta
# como volumen en runtime (ver ticket-ai-infra/docker-compose.yml). Montar un
# named volume directo sobre un solo archivo (en vez de un directorio) resultó
# no ser confiable en este Docker Engine ("... is not directory" al crear el
# contenedor), así que se monta sobre un directorio -- mismo patrón que ya usa
# pocketbase/Dockerfile con /pb_data.
RUN mkdir -p data

# Expose FastAPI default port
EXPOSE 8000 8501

# Start the FastAPI app with uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]