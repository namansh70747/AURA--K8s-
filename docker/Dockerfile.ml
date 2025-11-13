FROM python:3.11-slim

WORKDIR /app

# Install build dependencies for Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies for ML
COPY ml/train/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy all ML code
COPY ml/ ./ml/

# Set Python path
ENV PYTHONPATH=/app

# Default command for testing
CMD ["python3", "ml/test_ml_models.py"]
