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

- **Öngyógyítás (Self-Healing):** Automatikus konténer újraindítás, ha hibával áll le.
- **Hálózati monitorozás:** Figyeli a forgalmat (Mbps) és riaszt, ha túl magas.
- **Log figyelés:** Képes log fájlokban keresni kritikus hibákat (ERROR, FATAL).
- **Csendes mód (Quiet Hours):** Éjszaka csak a kritikus riasztások jönnek meg.
- **YAML konfiguráció:** Könnyen kezelhető beállítások fájlból.

## Használat Docker Compose-al (Ajánlott)

A legkényelmesebb futtatási mód a Docker Compose használata.

1. Másold le a `docker-compose.yml` fájlt.
2. Hozz létre egy `config.yaml` fájlt a `config.yaml.example` alapján.
3. Indítsd el:
   ```bash
   docker-compose up -d
   ```

### Megjegyzés a jogosultságokról
A Docker socket (`/var/run/docker.sock`) eléréséhez a konténernek megfelelő jogosultságokkal kell rendelkeznie. Ha `[Errno 13] Permission denied` hibát látsz a logokban:
1. Ellenőrizd a host gépen a docker csoport GID-jét: `getent group docker | cut -d: -f3`
2. Állítsd be a `docker-compose.yml`-ben a `user` mezőt (pl. `user: "1000:999"`, ahol 999 a kapott GID).
3. Vagy futtasd a konténert rootként: `user: "root"` (kevésbé biztonságos).

### docker-compose.yml példa:
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
      - ./config.yaml:/app/config.yaml:ro
    environment:
      - TZ=Europe/Budapest
      - CONFIG_PATH=/app/config.yaml
```

## Konfiguráció (YAML)

A `config.yaml` fájlban minden paraméter finomhangolható:

| Szakasz | Paraméter | Leírás | Alapértelmezett |
|---------|-----------|--------|-----------------|
| `ntfy` | `url` | ntfy topic URL | - |
| `limits` | `temp` | CPU hőmérséklet limit (°C) | `82` |
| `limits` | `ram` | RAM használat limit (%) | `92` |
| `limits` | `net_mbps` | Hálózati forgalom limit (Mbps) | `100` |
| `monitoring` | `quiet_hours` | Csendes időszak (pl. "23:00-06:00") | `null` |
| `docker` | `auto_restart` | Automatikus újraindítás | `false` |

## Környezeti változók
A visszamenőleges kompatibilitás érdekében az alábbi környezeti változók is használhatóak:

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
