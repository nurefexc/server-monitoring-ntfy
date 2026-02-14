import os
import time
import requests
import schedule
import logging
import threading
import json
import socket
from datetime import datetime
from typing import Dict, Tuple, List, Optional

"""
Server monitoring application with ntfy notifications.
Monitors system resources (CPU temperature, RAM, Disk) and Docker containers.
"""

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='{asctime} [{levelname}] {message}',
    style='{',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("NomadMonitor")

# --- CONFIGURATION ---
# ntfy settings
NTFY_URL: str = os.getenv("NTFY_URL", "")
NTFY_TOKEN: Optional[str] = os.getenv("NTFY_TOKEN")
HOSTNAME: str = os.getenv("HOSTNAME", socket.gethostname())

# Thresholds and timings
TEMP_LIMIT: int = int(os.getenv("TEMP_LIMIT", "82"))
DISK_LIMIT: int = int(os.getenv("DISK_LIMIT", "90"))
RAM_LIMIT: int = int(os.getenv("RAM_LIMIT", "92"))
DAILY_TIME: str = os.getenv("DAILY_TIME", "08:00")
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "60"))

# Global state
current_disk_stats: Dict[str, float] = {}


def send_ntfy(title: str, message: str, priority: str = "3", tags: str = "bar_chart") -> None:
    """
    Send notification via ntfy.
    Cleans headers to ASCII format to avoid encoding errors.
    
    Args:
        title (str): Notification title.
        message (str): Notification body.
        priority (str): ntfy priority (1-5).
        tags (str): ntfy tags (for emojis).
    """
    if not NTFY_URL:
        logger.error("NTFY_URL is not configured!")
        return

    # ASCII sanitization for headers (Title must not contain Emojis)
    # The tags will provide the visual icons (skull, fire, etc.)
    safe_title = title.encode('ascii', 'ignore').decode('ascii').strip()
    clean_title = f"{safe_title} | {HOSTNAME}"
    clean_message = message.replace('\0', '').strip()

    headers = {
        "Title": clean_title,
        "Priority": str(priority),
        "Tags": tags
    }

    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    try:
        # Message body is sent as raw UTF-8 bytes - this is safe for emojis
        response = requests.post(
            NTFY_URL,
            data=clean_message.encode('utf-8'),
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        logger.info(f"Notification sent: {title}")
    except Exception as e:
        logger.error(f"Ntfy error: {e}")


# --- DOCKER EVENT MONITORING ---
def monitor_docker_events() -> None:
    """
    Real-time Docker event monitoring via Docker Socket.
    Monitors unexpected container stops (die event).
    """
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        logger.warning("Docker socket not found at /var/run/docker.sock. Container monitoring disabled.")
        return

    logger.info("Docker Event Monitor thread active.")
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(socket_path)
                # API query for container 'die' events
                filter_query = '%7B%22type%22%3A%5B%22container%22%5D%2C%22event%22%3A%5B%22die%22%5D%7D'
                request = f"GET /events?filters={filter_query} HTTP/1.0\r\n\r\n"
                s.send(request.encode())

                with s.makefile('r', encoding='utf-8') as f:
                    for line in f:
                        clean_line = line.replace('\0', '').strip()
                        if not clean_line:
                            continue
                        try:
                            event = json.loads(clean_line)
                            attr = event.get('Actor', {}).get('Attributes', {})
                            c_name = attr.get('name', 'Unknown')
                            exit_code = attr.get('exitCode', '0')

                            if exit_code != "0":
                                msg = f"Container '{c_name}' crashed (Exit Code: {exit_code})"
                                logger.warning(msg)
                                send_ntfy("CONTAINER CRASHED", msg, "5", "skull,warning")
                        except (json.JSONDecodeError, ValueError):
                            continue
        except Exception as e:
            logger.error(f"Docker socket connection error: {e}")
            time.sleep(10)


# --- SYSTEM STATISTICS ---
def get_disks_usage() -> Dict[str, float]:
    """
    Retrieves disk usage for physical devices.
    Filters out virtual filesystems related to Docker and the system.
    """
    global current_disk_stats
    disks: Dict[str, float] = {}
    try:
        if not os.path.exists("/proc/mounts"):
            return disks

        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.split()
                # Monitor only physical/mapped drives
                if len(parts) >= 2 and parts[0].startswith(('/dev/sd', '/dev/nvme', '/dev/mapper')):
                    mount = parts[1]
                    # Filter out Docker/K8s specific mount points
                    if mount not in disks and not any(x in mount for x in ['docker', 'overlay', 'kubelet', 'containers']):
                        try:
                            st = os.statvfs(mount)
                            if st.f_blocks > 0:
                                usage = round((1 - (st.f_bavail / st.f_blocks)) * 100, 1)
                                disks[mount] = usage
                        except OSError:
                            continue
        current_disk_stats = disks
        logger.info(f"Disk check completed: {disks}")
    except Exception as e:
        logger.error(f"Disk scanning error: {e}")
    return disks


def get_system_stats() -> Tuple[float, float, float]:
    """
    Retrieves CPU temperature, RAM usage, and system load.
    
    Returns:
        Tuple[float, float, float]: (temperature, ram_usage_percent, load_avg)
    """
    temp, ram, load = 0.0, 0.0, 0.0
    
    # CPU Temperature
    try:
        # Try standard hwmon paths
        temp_found = False
        for i in range(10):
            path = f"/sys/class/hwmon/hwmon{i}/temp1_input"
            if os.path.exists(path):
                with open(path, "r") as f:
                    temp = int(f.read()) / 1000
                    temp_found = True
                    break
        if not temp_found and os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp = int(f.read()) / 1000
    except Exception as e:
        logger.debug(f"Could not read temperature: {e}")

    # RAM Usage
    try:
        with open('/proc/meminfo', 'r') as f:
            m = {l.split(':')[0]: l.split(':')[1].strip() for l in f}
        if 'MemTotal' in m and 'MemAvailable' in m:
            total = int(m['MemTotal'].split()[0])
            available = int(m['MemAvailable'].split()[0])
            ram = round((1 - (available / total)) * 100, 1)
    except Exception as e:
        logger.debug(f"Could not read RAM info: {e}")

    # Load Average
    try:
        load = os.getloadavg()[0]
    except Exception:
        pass

    return temp, ram, load


def check_critical_fast() -> None:
    """
    Quick check for critical values (Temperature, RAM).
    """
    temp, ram, load = get_system_stats()
    issues: List[str] = []
    
    if temp >= TEMP_LIMIT and temp > 0:
        issues.append(f"CPU Overheat: {temp}C")
    if ram >= RAM_LIMIT:
        issues.append(f"High RAM Usage: {ram}%")

    if issues:
        send_ntfy("CRITICAL ALERT", "\n".join(issues), "5", "fire,warning")
    else:
        logger.info(f"Health OK: {temp}C | RAM: {ram}% | Load: {load:.2f}")


def check_critical_disks() -> None:
    """
    Checks disk usage based on the specified threshold.
    """
    disks = get_disks_usage()
    for path, used in disks.items():
        if used >= DISK_LIMIT:
            send_ntfy("STORAGE ALERT", f"Low Space on {path}: {used}%", "4", "floppy_disk")


def send_report(report_type: str = "Daily") -> None:
    """
    Sends a summary status report.
    """
    temp, ram, load = get_system_stats()
    disk_info = "\n".join([f"- {path}: {used}%" for path, used in current_disk_stats.items()])
    summary = (
        f"Status: Operational\n"
        f"Temp: {temp if temp > 0 else 'N/A'}C\n"
        f"RAM: {ram}%\n"
        f"Load: {load:.2f}\n\n"
        f"Disks:\n{disk_info if disk_info else 'None detected'}"
    )
    send_report_title = f"{report_type} Status"
    send_ntfy(send_report_title, summary, "3", "calendar")


# --- MAIN LOOP ---
if __name__ == "__main__":
    logger.info(f"--- Server Monitor Starting on {HOSTNAME} ---")
    
    # Initialize disk stats
    get_disks_usage()

    # Start Docker monitor on background thread
    docker_thread = threading.Thread(target=monitor_docker_events, daemon=True)
    docker_thread.start()

    # Setup scheduling
    schedule.every().day.at(DAILY_TIME).do(send_report, report_type="Daily")
    schedule.every().monday.at(DAILY_TIME).do(send_report, report_type="Weekly")
    schedule.every().hour.do(check_critical_disks)

    logger.info(f"Monitoring started. Interval: {CHECK_INTERVAL}s")

    while True:
        try:
            check_critical_fast()
            schedule.run_pending()
        except Exception as e:
            logger.critical(f"Error in main loop: {e}")
        time.sleep(CHECK_INTERVAL)
