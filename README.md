# Server Monitoring ntfy

[![Docker Hub](https://img.shields.io/docker/pulls/nurefexc/server-monitoring-ntfy.svg)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)
[![Docker Image Size](https://img.shields.io/docker/image-size/nurefexc/server-monitoring-ntfy/latest)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)
[![Docker Image Version](https://img.shields.io/docker/v/nurefexc/server-monitoring-ntfy/latest)](https://hub.docker.com/r/nurefexc/server-monitoring-ntfy)

A lightweight Python script that monitors system resources, Docker containers, external services, and logs, sending real-time alerts and scheduled reports to your [ntfy](https://ntfy.sh) topic.

## Features

- **System Resource Monitoring:** Tracks CPU temperature, RAM, CPU %, and Disk space with dynamic thresholds (Warning/Critical).
- **Docker Event Monitoring:** Instant notifications when a container stops unexpectedly or becomes unhealthy.
- **External Service Checks:** Monitors URLs for availability and expected HTTP status codes.
- **SSL Certificate Monitoring:** Alerts before your SSL certificates expire.
- **SSH & Log Monitoring:** Uses Regex to watch log files for SSH logins or specific error patterns.
- **Backup Verification:** Ensures your backup files are present and up-to-date.
- **Scheduled Reports:** Automatically sends Daily status summaries.
- **Quiet Hours:** Suppresses non-critical notifications during specified hours.
- **Self-Healing:** Can automatically restart crashed Docker containers.
- **Async Implementation:** Modern `asyncio` base for high performance and low resource usage.

## Usage with Docker Compose (Recommended)

1. Copy the `docker-compose.yml` file.
2. Create a `config.yaml` based on `config.yaml.example`.
3. Start the monitor:
   ```bash
   docker-compose up -d
   ```

### Permission Notes
To access the Docker socket (`/var/run/docker.sock`), the container needs proper permissions. If you see `[Errno 13] Permission denied` in logs:
1. Check the docker group GID on the host: `getent group docker | cut -d: -f3`
2. Set the `user` field in `docker-compose.yml` (e.g., `user: "1000:999"`, where 999 is the GID).
3. Alternatively, run as root: `user: "root"` (less secure but easiest).

### docker-compose.yml example:
```yaml
services:
  monitoring:
    image: nurefexc/server-monitoring-ntfy:latest
    container_name: server-monitor
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc:/proc:ro
      - /sys:/sys:ro
      - /var/log:/var/log:ro
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - TZ=UTC
      - CONFIG_PATH=/app/config.yaml
```

## Configuration (YAML)

Everything is configurable in `config.yaml`. See `config.yaml.example` for a full list of options including log patterns and service checks.

| Section | Parameter | Description | Default |
|---------|-----------|--------|-----------------|
| `ntfy` | `url` | ntfy topic URL | - |
| `limits` | `temp` | CPU temperature limits | `75/85` |
| `limits` | `ram` | RAM usage limits | `80/90` |
| `monitoring` | `quiet_hours` | Period for critical-only alerts | `null` |
| `docker` | `auto_restart` | Auto restart crashed containers | `false` |

## Notification Types Supported

The monitor handles various events with custom icons and priorities:
- 🔥 **System Alert** (Priority 4/5) - Resource thresholds exceeded.
- 💾 **Storage Alert** (Priority 4/5) - Disk space or Inodes running low.
- 💀 **Container Crash** (Priority 5) - Docker container exited with an error.
- ☁️ **Service Down** (Priority 5) - External URL check failed.
- 🔒 **SSL/SSH Alert** (Priority 4) - SSL expiring or new SSH login detected.
- 📅 **Scheduled Status** (Priority 3) - Daily system health summary.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
