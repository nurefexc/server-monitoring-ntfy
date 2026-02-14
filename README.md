# Server Monitoring ntfy

[![Docker Hub](https://img.shields.io/docker/pulls/nurefexc/server-monitoring-ntfy.svg)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)
[![Docker Image Size](https://img.shields.io/docker/image-size/nurefexc/server-monitoring-ntfy/latest)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)
[![Docker Image Version](https://img.shields.io/docker/v/nurefexc/server-monitoring-ntfy/latest)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)

A lightweight Python script that monitors system resources and Docker containers, sending real-time alerts and scheduled reports to your [ntfy](https://ntfy.sh) topic.

## Features

- **System Resource Monitoring:** Tracks CPU temperature, RAM usage, and Disk space with customizable thresholds.
- **Docker Event Monitoring:** Instant notifications when a container stops unexpectedly (detects non-zero exit codes via Docker socket).
- **Scheduled Reports:** Automatically sends Daily and Weekly status summaries to keep you informed.
- **Priority-Based Alerts:** Uses ntfy priorities (1-5) and tags (emojis) to distinguish between critical issues and routine updates.
- **Docker Ready:** Optimized for containerized deployment with minimal footprint.

## Prerequisites

1. **ntfy Topic:** Create a topic on [ntfy.sh](https://ntfy.sh) (e.g., `my_server_monitor`).
2. **Docker Socket (Optional):** To monitor container crashes, the script needs access to `/var/run/docker.sock`.

## Setup & Installation

### Option 1: Using Docker (Recommended)

The easiest way to run the monitor is using the official Docker image:

1. Pull the image from Docker Hub:
   ```bash
   docker pull nurefexc/server-monitoring-ntfy:latest
   ```
2. Run the container:
   ```bash
   docker run -d \
     --name server-monitor \
     --restart always \
     -v /var/run/docker.sock:/var/run/docker.sock:ro \
     -e NTFY_URL=https://ntfy.sh/your_topic \
     nurefexc/server-monitoring-ntfy:latest
   ```
   *Note: Mounting the Docker socket as read-only (`:ro`) is required for container monitoring.*

### Option 2: Build Locally
If you want to build the image yourself:
1. Clone this repository.
2. Build the image:
   ```bash
   docker build -t nurefexc/server-monitoring-ntfy:latest .
   ```
3. Run the container as shown in Option 1.

## CI/CD (Automation)

This repository includes a GitHub Action that automatically builds and pushes the Docker image to **Docker Hub** whenever you push to the `master` branch.

To enable this, add the following **Secrets** to your GitHub repository (`Settings > Secrets and variables > Actions`):
- `DOCKERHUB_USERNAME`: Your Docker Hub username.
- `DOCKERHUB_TOKEN`: Your Docker Hub Personal Access Token (PAT).

### Option 3: Manual Installation

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set environment variables (see `.env.sample`).
3. Run the script:
   ```bash
   python main.py
   ```

## Configuration

The following environment variables are supported:

| Variable | Description | Default |
|----------|-------------|---------|
| `NTFY_URL` | Full ntfy topic URL (required) | - |
| `NTFY_TOKEN` | ntfy authentication token (optional) | - |
| `HOSTNAME` | Server name shown in notifications | *System Hostname* |
| `TEMP_LIMIT` | CPU temperature alert threshold (°C) | `82` |
| `DISK_LIMIT` | Disk usage alert threshold (%) | `90` |
| `RAM_LIMIT` | RAM usage alert threshold (%) | `92` |
| `DAILY_TIME` | Time for scheduled reports (HH:MM) | `08:00` |
| `CHECK_INTERVAL`| Polling frequency in seconds | `60` |
| `TZ` | System timezone (e.g., `Europe/Budapest`) | `UTC` |

## Notification Types Supported

The monitor handles various events with custom icons and priorities:
- 🔥 **Critical Alert** (Priority 5) - CPU or RAM threshold exceeded.
- 💾 **Storage Alert** (Priority 4) - Disk space running low.
- 💀 **Container Crash** (Priority 5) - Docker container exited with an error.
- 📅 **Scheduled Status** (Priority 3) - Daily or Weekly system health summary.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
