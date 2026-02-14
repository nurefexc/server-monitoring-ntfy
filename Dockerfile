FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

LABEL maintainer="nurefexc"
LABEL description="Server monitoring application with ntfy notifications"

WORKDIR /app

# Install dependencies and tzdata
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Run the application
CMD ["python", "main.py"]
