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

# Create a non-root user
RUN groupadd -r monitor && useradd -r -g monitor monitor && \
    groupadd -g 999 docker_host || true && \
    usermod -aG docker_host monitor || true

# Copy application code
COPY main.py .

# Ensure the user has access to the app directory
RUN chown -R monitor:monitor /app

# Run as non-root
USER monitor

# Run the application
CMD ["python", "main.py"]
