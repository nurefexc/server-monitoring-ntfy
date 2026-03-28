# --- Builder Stage ---
FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Final Stage ---
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

LABEL maintainer="nurefexc"
LABEL description="Server monitoring application with ntfy notifications"

WORKDIR /app

# Install tzdata and procps (for healthcheck) and systemd (for journalctl)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata procps systemd && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r monitor && useradd -r -g monitor -m monitor && \
    (groupadd -g 999 docker_host || groupadd docker_host) && \
    usermod -aG docker_host monitor

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY main.py .
RUN chown monitor:monitor main.py

# Run as non-root
USER monitor

# Healthcheck
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD find /tmp/heartbeat -mmin -2 | grep . || exit 1

# Run the application
CMD ["python", "main.py"]
