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

# Expose FastAPI default port
EXPOSE 8000 8501

# Start the FastAPI app with uvicorn
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]