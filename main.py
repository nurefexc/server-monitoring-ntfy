import os
import time
import requests
import schedule
import logging
import threading
import json
import socket
import yaml
from datetime import datetime
from typing import Dict, Tuple, List, Optional, Any

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
DEFAULT_CONFIG = {
    "ntfy": {
        "url": "",
        "token": None,
        "priority": "3",
        "tags": "bar_chart"
    },
    "limits": {
        "temp": 82,
        "disk": 90,
        "ram": 92,
        "net_mbps": 100,  # New: Network limit in Mbps
    },
    "monitoring": {
        "hostname": socket.gethostname(),
        "timezone": "UTC",
        "daily_time": "08:00",
        "check_interval": 60,
        "quiet_hours": None, # Format: "23:00-06:00"
    },
    "docker": {
        "auto_restart": False,
        "monitor_health": True,
    },
    "logs": {
        "watch_files": [], # List of files to monitor for "ERROR", "FATAL"
    }
}

def load_config() -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config_path = os.getenv("CONFIG_PATH", "config.yaml")
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = yaml.safe_load(f)
                if user_config:
                    # Deep merge would be better, but simple for now
                    for section, values in user_config.items():
                        if section in config and isinstance(config[section], dict):
                            config[section].update(values)
                        else:
                            config[section] = values
            logger.info(f"Configuration loaded from {config_path}")
        except Exception as e:
            logger.error(f"Error loading config file: {e}")

    # Override with Environment Variables (backwards compatibility)
    if os.getenv("NTFY_URL"): config["ntfy"]["url"] = os.getenv("NTFY_URL")
    if os.getenv("NTFY_TOKEN"): config["ntfy"]["token"] = os.getenv("NTFY_TOKEN")
    if os.getenv("HOSTNAME"): config["monitoring"]["hostname"] = os.getenv("HOSTNAME")
    if os.getenv("TZ"): config["monitoring"]["timezone"] = os.getenv("TZ")
    if os.getenv("TEMP_LIMIT"): config["limits"]["temp"] = int(os.getenv("TEMP_LIMIT"))
    if os.getenv("DISK_LIMIT"): config["limits"]["disk"] = int(os.getenv("DISK_LIMIT"))
    if os.getenv("RAM_LIMIT"): config["limits"]["ram"] = int(os.getenv("RAM_LIMIT"))
    if os.getenv("DAILY_TIME"): config["monitoring"]["daily_time"] = os.getenv("DAILY_TIME")
    if os.getenv("CHECK_INTERVAL"): config["monitoring"]["check_interval"] = int(os.getenv("CHECK_INTERVAL"))
    if os.getenv("AUTO_RESTART"): config["docker"]["auto_restart"] = os.getenv("AUTO_RESTART").lower() == "true"

    # Set local timezone
    os.environ['TZ'] = config["monitoring"]["timezone"]
    if hasattr(time, 'tzset'):
        try:
            time.tzset()
        except Exception:
            pass

    return config

config = load_config()

# Global state
current_disk_stats: Dict[str, float] = {}
last_net_bytes: Dict[str, Tuple[float, int]] = {} # {interface: (timestamp, bytes)}

def is_quiet_hours() -> bool:
    qh = config["monitoring"].get("quiet_hours")
    if not qh:
        return False
    try:
        start_str, end_str = qh.split('-')
        now = datetime.now().time()
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
        
        if start <= end:
            return start <= now <= end
        else: # Over midnight
            return now >= start or now <= end
    except Exception as e:
        logger.error(f"Error parsing quiet hours: {e}")
        return False

def send_ntfy(title: str, message: str, priority: str = None, tags: str = None) -> None:
    """
    Send notification via ntfy.
    """
    ntfy_url = config["ntfy"]["url"]
    if not ntfy_url:
        logger.error("NTFY_URL is not configured!")
        return

    # Use defaults from config if not provided
    priority = priority or config["ntfy"]["priority"]
    tags = tags or config["ntfy"]["tags"]

    # Quiet hours check: only send priority 5 if in quiet hours
    if is_quiet_hours() and int(priority) < 5:
        logger.info(f"Quiet hours active. Suppressing notification: {title}")
        return

    safe_title = title.encode('ascii', 'ignore').decode('ascii').strip()
    clean_title = f"{safe_title} | {config['monitoring']['hostname']}"
    clean_message = message.replace('\0', '').strip()

    headers = {
        "Title": clean_title,
        "Priority": str(priority),
        "Tags": tags
    }

    if config["ntfy"]["token"]:
        headers["Authorization"] = f"Bearer {config['ntfy']['token']}"

    try:
        # Message body is sent as raw UTF-8 bytes - this is safe for emojis
        response = requests.post(
            ntfy_url,
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
    """
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        logger.warning(f"Docker socket not found at {socket_path}. Container monitoring disabled.")
        return
    
    if not os.access(socket_path, os.R_OK | os.W_OK):
        logger.error(f"Permission denied for Docker socket at {socket_path}. Check your user/group IDs.")
        # We don't return here to allow the while loop to retry (maybe permissions change?)
        # but we could also return if we want to stop early.

    logger.info("Docker Event Monitor thread active.")
    
    # Build filter query based on config
    actions = ["die", "start"]
    if config["docker"].get("monitor_health"):
        actions.append("health_status")
    
    # URL encode filters
    filters = json.dumps({"type": ["container"], "action": actions})
    from urllib.parse import quote
    filter_query = quote(filters)

    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(socket_path)
                request = f"GET /events?filters={filter_query} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
                s.send(request.encode())

                with s.makefile('r', encoding='utf-8') as f:
                    # Skip HTTP headers
                    for line in f:
                        if line == "\r\n": break
                    
                    for line in f:
                        clean_line = line.replace('\0', '').strip()
                        if not clean_line:
                            continue
                        try:
                            # Docker events are often sent in chunks or as multiple JSONs
                            # Simple approach: if it starts with { it might be our event
                            if not clean_line.startswith('{'): continue
                            
                            event = json.loads(clean_line)
                            action = event.get('action')
                            actor = event.get('Actor', {})
                            c_id = actor.get('ID', 'Unknown')
                            attr = actor.get('Attributes', {})
                            c_name = attr.get('name', 'Unknown')
                            
                            if action == "die":
                                exit_code = attr.get('exitCode', '0')
                                if exit_code != "0":
                                    msg = f"Container '{c_name}' crashed (Exit Code: {exit_code})"
                                    logger.warning(msg)
                                    send_ntfy("CONTAINER CRASHED", msg, "5", "skull,warning")
                                    
                                    if config["docker"].get("auto_restart"):
                                        logger.info(f"Attempting to restart container: {c_name}")
                                        restart_container(c_id)
                                else:
                                    msg = f"Container '{c_name}' stopped gracefully"
                                    logger.info(msg)
                                    send_ntfy("CONTAINER STOPPED", msg, "3", "stop_button")
                            elif action == "start":
                                msg = f"Container '{c_name}' started"
                                logger.info(msg)
                                send_ntfy("CONTAINER STARTED", msg, "3", "rocket")
                            elif action.startswith("health_status"):
                                status = action.split(":")[-1].strip()
                                if status == "unhealthy":
                                    msg = f"Container '{c_name}' is UNHEALTHY"
                                    logger.warning(msg)
                                    send_ntfy("CONTAINER UNHEALTHY", msg, "4", "medical_symbol,warning")
                        except (json.JSONDecodeError, ValueError):
                            continue
        except Exception as e:
            logger.error(f"Docker socket connection error: {e}")
            time.sleep(10)


def restart_container(container_id: str) -> None:
    """
    Attempts to restart a container via Docker Socket.
    """
    socket_path = "/var/run/docker.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(socket_path)
            request = f"POST /containers/{container_id}/restart HTTP/1.1\r\nHost: docker\r\nContent-Length: 0\r\n\r\n"
            s.send(request.encode())
            response = s.recv(4096).decode()
            if "204 No Content" in response or "200 OK" in response:
                logger.info(f"Container {container_id} restart command sent successfully.")
            else:
                logger.error(f"Failed to restart container {container_id}: {response}")
    except Exception as e:
        logger.error(f"Error restarting container {container_id}: {e}")


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


def get_network_usage() -> Optional[float]:
    """
    Calculates average network throughput in Mbps.
    """
    global last_net_bytes
    try:
        if not os.path.exists("/proc/net/dev"):
            return None
        
        current_time = time.time()
        total_bytes = 0
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:] # Skip headers
            for line in lines:
                parts = line.split()
                if len(parts) > 8:
                    iface = parts[0].strip(':')
                    if iface == 'lo': continue
                    # receive bytes + transmit bytes
                    total_bytes += int(parts[1]) + int(parts[9])
        
        if not last_net_bytes:
            last_net_bytes["total"] = (current_time, total_bytes)
            return 0.0
        
        last_time, last_total = last_net_bytes["total"]
        time_diff = current_time - last_time
        if time_diff <= 0: return 0.0
        
        byte_diff = total_bytes - last_total
        if byte_diff < 0: # Counter reset
            last_net_bytes["total"] = (current_time, total_bytes)
            return 0.0
        
        mbps = (byte_diff * 8) / (1024 * 1024) / time_diff
        last_net_bytes["total"] = (current_time, total_bytes)
        return round(mbps, 2)
    except Exception as e:
        logger.debug(f"Network stats error: {e}")
        return None


def get_system_stats() -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Retrieves CPU temperature, ram usage, system load, and network traffic.
    
    Returns:
        Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]: (temp, ram, load, net)
    """
    temp, ram, load, net = None, None, None, None
    
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

    net = get_network_usage()

    return temp, ram, load, net


def check_critical_fast() -> None:
    """
    Quick check for critical values (Temperature, RAM, Network).
    """
    temp, ram, load, net = get_system_stats()
    issues: List[str] = []
    
    if temp is not None and temp >= config["limits"]["temp"]:
        issues.append(f"CPU Overheat: {temp}C")
    if ram is not None and ram >= config["limits"]["ram"]:
        issues.append(f"High RAM Usage: {ram}%")
    if net is not None and net >= config["limits"]["net_mbps"]:
        issues.append(f"High Network Load: {net} Mbps")

    if issues:
        send_ntfy("CRITICAL ALERT", "\n".join(issues), "5", "fire,warning")
    else:
        stats_str = []
        if temp is not None: stats_str.append(f"Temp: {temp}C")
        if ram is not None: stats_str.append(f"RAM: {ram}%")
        if load is not None: stats_str.append(f"Load: {load:.2f}")
        if net is not None: stats_str.append(f"Net: {net} Mbps")
        logger.info(f"Health OK: {' | '.join(stats_str)}")


def check_critical_disks() -> None:
    """
    Checks disk usage based on the specified threshold.
    """
    disks = get_disks_usage()
    for path, used in disks.items():
        if used >= config["limits"]["disk"]:
            send_ntfy("STORAGE ALERT", f"Low Space on {path}: {used}%", "4", "floppy_disk")


def send_report(report_type: str = "Daily") -> None:
    """
    Sends a summary status report.
    """
    temp, ram, load, net = get_system_stats()
    
    report_lines = ["Status: Operational"]
    if temp is not None:
        report_lines.append(f"Temp: {temp}C")
    if ram is not None:
        report_lines.append(f"RAM: {ram}%")
    if load is not None:
        report_lines.append(f"Load: {load:.2f}")
    if net is not None:
        report_lines.append(f"Net: {net} Mbps")
    
    disk_info = "\n".join([f"- {path}: {used}%" for path, used in current_disk_stats.items()])
    if disk_info:
        report_lines.append(f"\nDisks:\n{disk_info}")
    else:
        report_lines.append("\nDisks: None detected")

    summary = "\n".join(report_lines)
    send_report_title = f"{report_type} Status"
    send_ntfy(send_report_title, summary, "3", "calendar")


def monitor_logs() -> None:
    """
    Monitors log files for critical keywords.
    """
    watch_files = config["logs"].get("watch_files", [])
    if not watch_files:
        return

    logger.info(f"Log Monitor active for files: {watch_files}")
    
    threads = []
    for file_path in watch_files:
        t = threading.Thread(target=tail_file, args=(file_path,), daemon=True)
        t.start()
        threads.append(t)


def tail_file(file_path: str) -> None:
    """
    Simple tail -f implementation to watch for keywords.
    """
    if not os.path.exists(file_path):
        logger.warning(f"Log file not found: {file_path}")
        return

    try:
        with open(file_path, 'r') as f:
            # Go to end of file
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(1)
                    continue
                
                # Check for keywords
                upper_line = line.upper()
                if "ERROR" in upper_line or "FATAL" in upper_line or "CRITICAL" in upper_line:
                    msg = f"Found in {file_path}: {line.strip()}"
                    logger.warning(f"Log Alert: {msg}")
                    send_ntfy("LOG ALERT", msg, "4", "mag_right,warning")
    except Exception as e:
        logger.error(f"Error watching log file {file_path}: {e}")


# --- MAIN LOOP ---
if __name__ == "__main__":
    mon_cfg = config["monitoring"]
    logger.info(f"--- Server Monitor Starting on {mon_cfg['hostname']} ---")
    
    # Initialize disk stats
    get_disks_usage()

    # Start Docker monitor on background thread
    docker_thread = threading.Thread(target=monitor_docker_events, daemon=True)
    docker_thread.start()

    # Start Log monitor
    monitor_logs()

    # Setup scheduling
    schedule.every().day.at(mon_cfg["daily_time"]).do(send_report, report_type="Daily")
    schedule.every().monday.at(mon_cfg["daily_time"]).do(send_report, report_type="Weekly")
    schedule.every().hour.do(check_critical_disks)

    interval = mon_cfg["check_interval"]
    logger.info(f"Monitoring started. Interval: {interval}s")

    while True:
        try:
            check_critical_fast()
            schedule.run_pending()
        except Exception as e:
            logger.critical(f"Error in main loop: {e}")
        time.sleep(interval)
