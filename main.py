import os
import time
import asyncio
import aiohttp
import yaml
import json
import socket
import logging
import re
import ssl
from datetime import datetime, timezone
from typing import Dict, Tuple, List, Optional, Any
from cryptography import x509
from cryptography.hazmat.backends import default_backend

"""
Advanced Server Monitoring Application with ntfy notifications.
Supports system resources, Docker, external services, log patterns, and SSL.
"""

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='{asctime} [{levelname}] {message}',
    style='{',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ServerMonitor")

# --- DEFAULT CONFIGURATION ---
DEFAULT_CONFIG = {
    "ntfy": {
        "url": "",
        "token": None,
        "priority": "3",
        "tags": "bar_chart"
    },
    "limits": {
        "temp": {"warning": 75, "critical": 85},
        "disk": {"warning": 85, "critical": 95},
        "ram": {"warning": 80, "critical": 90},
        "net_mbps": 100,
        "swap": 80,
        "inode": 90,
    },
    "monitoring": {
        "hostname": socket.gethostname(),
        "timezone": "UTC",
        "daily_time": "08:00",
        "check_interval": 60,
        "quiet_hours": None,
        "heartbeat_file": "/tmp/heartbeat",
    },
    "docker": {
        "auto_restart": False,
        "monitor_health": True,
    },
    "services": [],
    "logs": {
        "watch_files": [],
    },
    "backups": []
}

def load_config() -> Dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config_path = os.getenv("CONFIG_PATH", "config.yaml")
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = yaml.safe_load(f)
                if user_config:
                    for section, values in user_config.items():
                        if section in config and isinstance(config[section], dict):
                            config[section].update(values)
                        else:
                            config[section] = values
            logger.info(f"Configuration loaded from {config_path}")
        except Exception as e:
            logger.error(f"Error loading config file: {e}")

    # Legacy Environment Variable Overrides
    if os.getenv("NTFY_URL"): config["ntfy"]["url"] = os.getenv("NTFY_URL")
    if os.getenv("NTFY_TOKEN"): config["ntfy"]["token"] = os.getenv("NTFY_TOKEN")
    if os.getenv("HOSTNAME"): config["monitoring"]["hostname"] = os.getenv("HOSTNAME")
    if os.getenv("TZ"): config["monitoring"]["timezone"] = os.getenv("TZ")
    if os.getenv("DAILY_TIME"): config["monitoring"]["daily_time"] = os.getenv("DAILY_TIME")
    if os.getenv("CHECK_INTERVAL"): config["monitoring"]["check_interval"] = int(os.getenv("CHECK_INTERVAL") or 60)

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
current_inode_stats: Dict[str, float] = {}
last_net_bytes: Dict[str, Tuple[float, int]] = {} 
last_cpu_times: Tuple[int, int] = (0, 0)
daily_report_sent_date: Optional[str] = None

# --- UTILS ---
def update_heartbeat() -> None:
    hb_path = config["monitoring"].get("heartbeat_file")
    if hb_path:
        try:
            with open(hb_path, 'w') as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

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

async def send_ntfy(title: str, message: str, priority: str = None, tags: str = None, actions: List[Dict] = None) -> None:
    """Sends notification via ntfy using aiohttp."""
    ntfy_url = config["ntfy"]["url"]
    if not ntfy_url:
        logger.error("NTFY_URL not configured!")
        return

    priority = priority or config["ntfy"]["priority"]
    tags = tags or config["ntfy"]["tags"]

    if is_quiet_hours() and int(priority) < 5:
        logger.info(f"Quiet hours active. Suppressed: {title}")
        return

    clean_title = f"{title} | {config['monitoring']['hostname']}"
    
    headers = {
        "Title": clean_title,
        "Priority": str(priority),
        "Tags": tags
    }

    if config["ntfy"]["token"]:
        headers["Authorization"] = f"Bearer {config['ntfy']['token']}"
    
    if actions:
        headers["Actions"] = json.dumps(actions)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ntfy_url,
                data=message.encode('utf-8'),
                headers=headers,
                timeout=15
            ) as response:
                response.raise_for_status()
                logger.info(f"Notification sent: {title}")
    except Exception as e:
        logger.error(f"Ntfy error: {e}")

# --- SYSTEM STATS ---
def get_cpu_usage() -> Optional[float]:
    global last_cpu_times
    try:
        if not os.path.exists("/proc/stat"): return None
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = line.split()
        if len(parts) < 5: return None
        
        idle = int(parts[4]) + int(parts[5])
        total = sum(int(p) for p in parts[1:11])
        
        if last_cpu_times == (0, 0):
            last_cpu_times = (idle, total)
            return 0.0
        
        last_idle, last_total = last_cpu_times
        idle_diff = idle - last_idle
        total_diff = total - last_total
        last_cpu_times = (idle, total)
        
        if total_diff <= 0: return 0.0
        return round(100 * (1 - idle_diff / total_diff), 1)
    except Exception:
        return None

def get_network_usage() -> Optional[float]:
    global last_net_bytes
    try:
        if not os.path.exists("/proc/net/dev"): return None
        current_time = time.time()
        total_bytes = 0
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:]
            for line in lines:
                parts = line.split()
                if len(parts) > 8:
                    if parts[0].strip(':') == 'lo': continue
                    total_bytes += int(parts[1]) + int(parts[9])
        
        if "total" not in last_net_bytes:
            last_net_bytes["total"] = (current_time, total_bytes)
            return 0.0
        
        last_time, last_total = last_net_bytes["total"]
        time_diff = current_time - last_time
        if time_diff <= 0: return 0.0
        
        byte_diff = total_bytes - last_total
        last_net_bytes["total"] = (current_time, total_bytes)
        if byte_diff < 0: return 0.0
        
        mbps = (byte_diff * 8) / (1024 * 1024) / time_diff
        return round(mbps, 2)
    except Exception:
        return None

def get_system_stats() -> Dict[str, Any]:
    stats = {
        "temp": None, "cpu": get_cpu_usage(), "ram": None, 
        "swap": None, "load": None, "net": get_network_usage(), "uptime": None
    }
    
    # Temp
    try:
        for i in range(5):
            path = f"/sys/class/hwmon/hwmon{i}/temp1_input"
            if os.path.exists(path):
                with open(path, "r") as f:
                    stats["temp"] = int(f.read()) / 1000
                    break
        if stats["temp"] is None and os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                stats["temp"] = int(f.read()) / 1000
    except Exception: pass

    # RAM / Swap
    try:
        with open('/proc/meminfo', 'r') as f:
            m = {l.split(':')[0]: l.split(':')[1].strip() for l in f}
        if 'MemTotal' in m and 'MemAvailable' in m:
            total = int(m['MemTotal'].split()[0])
            available = int(m['MemAvailable'].split()[0])
            stats["ram"] = round((1 - (available / total)) * 100, 1)
        if 'SwapTotal' in m and 'SwapFree' in m and int(m['SwapTotal'].split()[0]) > 0:
            s_total = int(m['SwapTotal'].split()[0])
            s_free = int(m['SwapFree'].split()[0])
            stats["swap"] = round((1 - (s_free / s_total)) * 100, 1)
    except Exception: pass

    # Load / Uptime
    try:
        stats["load"] = os.getloadavg()[0]
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime", "r") as f:
                stats["uptime"] = float(f.read().split()[0])
    except Exception: pass

    return stats

async def check_disks():
    global current_disk_stats, current_inode_stats
    disks, inodes = {}, {}
    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[0].startswith(('/dev/sd', '/dev/nvme', '/dev/mapper')):
                    mount = p[1]
                    if not any(x in mount for x in ['docker', 'overlay', 'kubelet', 'containers']):
                        try:
                            st = os.statvfs(mount)
                            if st.f_blocks > 0:
                                disks[mount] = round((1 - (st.f_bavail / st.f_blocks)) * 100, 1)
                            if st.f_files > 0:
                                inodes[mount] = round((1 - (st.f_favail / st.f_files)) * 100, 1)
                        except OSError: continue
        current_disk_stats, current_inode_stats = disks, inodes
        
        issues = []
        limits = config["limits"]
        d_warn = limits["disk"].get("warning", 85)
        d_crit = limits["disk"].get("critical", 95)
        i_limit = limits.get("inode", 90)

        for path, used in disks.items():
            if used >= d_crit:
                issues.append(f"CRITICAL: Low space on {path}: {used}%")
            elif used >= d_warn:
                issues.append(f"WARNING: Low space on {path}: {used}%")
        
        for path, used in inodes.items():
            if used >= i_limit:
                issues.append(f"Low Inodes on {path}: {used}%")
        
        if issues:
            priority = "5" if any("CRITICAL" in i for i in issues) else "4"
            await send_ntfy("STORAGE ALERT", "\n".join(issues), priority, "floppy_disk,warning")
    except Exception as e:
        logger.error(f"Disk check error: {e}")

# --- DOCKER ---
async def docker_api_call(method: str, path: str) -> Optional[Any]:
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path): return None
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
        request = f"{method} {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        
        response = b""
        while True:
            data = await reader.read(4096)
            if not data: break
            response += data
        writer.close()
        await writer.wait_closed()
        
        parts = response.split(b"\r\n\r\n", 1)
        if len(parts) < 2: return None
        try:
            return json.loads(parts[1])
        except json.JSONDecodeError:
            return parts[1].decode('utf-8', errors='ignore')
    except Exception as e:
        logger.debug(f"Docker API error ({path}): {e}")
        return None

async def get_container_stats() -> List[str]:
    containers = await docker_api_call("GET", "/containers/json")
    if not isinstance(containers, list): return []
    
    results = []
    for c in containers:
        c_name = c['Names'][0].strip('/')
        stats = await docker_api_call("GET", f"/containers/{c['Id']}/stats?stream=false")
        if isinstance(stats, dict):
            mem_u = stats.get('memory_stats', {}).get('usage', 0)
            mem_l = stats.get('memory_stats', {}).get('limit', 1)
            mem_p = round((mem_u / mem_l) * 100, 1) if mem_l > 0 else 0
            
            c_delta = stats.get('cpu_stats', {}).get('cpu_usage', {}).get('total_usage', 0) - \
                      stats.get('precpu_stats', {}).get('cpu_usage', {}).get('total_usage', 0)
            s_delta = stats.get('cpu_stats', {}).get('system_cpu_usage', 0) - \
                      stats.get('precpu_stats', {}).get('system_cpu_usage', 0)
            cpus = stats.get('cpu_stats', {}).get('online_cpus', 1)
            cpu_p = round((c_delta / s_delta) * cpus * 100, 1) if s_delta > 0 else 0
            results.append(f"- {c_name}: CPU: {cpu_p}%, RAM: {mem_p}%")
    return results

async def monitor_docker_events():
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path): return
    
    actions = ["die", "start"]
    if config["docker"].get("monitor_health"): actions.append("health_status")
    filters = json.dumps({"type": ["container"], "action": actions})
    from urllib.parse import quote
    
    while True:
        try:
            reader, writer = await asyncio.open_unix_connection(socket_path)
            request = f"GET /events?filters={quote(filters)} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            
            while True:
                line = await reader.readline()
                if not line: break
                clean = line.decode().strip()
                if not clean.startswith('{'): continue
                
                event = json.loads(clean)
                action = event.get('action')
                if not action: continue

                attr = event.get('Actor', {}).get('Attributes', {})
                c_name = attr.get('name', 'Unknown')
                
                if action == "die":
                    exit_code = attr.get('exitCode', '0')
                    if exit_code != "0":
                        msg = f"Container '{c_name}' crashed (Exit Code: {exit_code})"
                        await send_ntfy("CONTAINER CRASHED", msg, "5", "skull,warning")
                        if config["docker"].get("auto_restart"):
                            await docker_api_call("POST", f"/containers/{event['Actor']['ID']}/restart")
                    else:
                        await send_ntfy("CONTAINER STOPPED", f"Container '{c_name}' stopped gracefully", "3", "stop_button")
                elif action == "start":
                    await send_ntfy("CONTAINER STARTED", f"Container '{c_name}' started", "3", "rocket")
                elif "health_status" in action and "unhealthy" in action:
                    await send_ntfy("CONTAINER UNHEALTHY", f"Container '{c_name}' is UNHEALTHY", "4", "medical_symbol,warning")
        except Exception as e:
            logger.error(f"Docker Event Socket error: {e}")
            await asyncio.sleep(10)

# --- SERVICES & SSL ---
async def check_services():
    async with aiohttp.ClientSession() as session:
        for svc in config.get("services", []):
            name = svc.get("name", "Unknown Service")
            url = svc.get("url")
            expected = svc.get("expected_status", 200)
            
            try:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != expected:
                        await send_ntfy("SERVICE DOWN", f"{name} returned status {resp.status} (expected {expected})", "5", "cloud_drain,warning")
                
                if svc.get("check_ssl") and url.startswith("https"):
                    await check_ssl_expiry(name, url, svc.get("ssl_days_before", 7))
            except Exception as e:
                await send_ntfy("SERVICE ERROR", f"{name} is unreachable: {e}", "5", "cloud_drain,warning")

async def check_ssl_expiry(name: str, url: str, days_limit: int):
    try:
        hostname = url.split("//")[-1].split("/")[0]
        context = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert_bin = ssock.getpeercert(True)
                cert = x509.load_der_x509_certificate(cert_bin, default_backend())
                now = datetime.now(timezone.utc)
                not_after = cert.not_valid_after_utc
                # Ensure not_after is timezone aware (it should be with not_valid_after_utc)
                if not_after.tzinfo is None:
                    not_after = not_after.replace(tzinfo=timezone.utc)
                
                remaining = not_after - now
                if remaining.days < days_limit:
                    await send_ntfy("SSL EXPIRY", f"Certificate for {name} ({hostname}) expires in {remaining.days} days!", "4", "lock,warning")
    except Exception as e:
        logger.error(f"SSL check error for {name}: {e}")

# --- BACKUPS ---
async def check_backups():
    for b in config.get("backups", []):
        name = b.get("name")
        path = b.get("path")
        max_age = b.get("max_age_hours", 24)
        
        if not os.path.exists(path):
            await send_ntfy("BACKUP MISSING", f"Backup file not found for {name} at {path}", "5", "card_file_box,warning")
            continue
        
        mtime = os.path.getmtime(path)
        age_hours = (time.time() - mtime) / 3600
        if age_hours > max_age:
            await send_ntfy("BACKUP STALE", f"Backup for {name} is {age_hours:.1f} hours old (limit: {max_age})", "5", "card_file_box,warning")

# --- LOGS ---
async def tail_log(file_cfg: Dict):
    path = file_cfg.get("path")
    patterns = file_cfg.get("patterns", [])
    if not os.path.exists(path):
        logger.warning(f"Log not found: {path}")
        return

    try:
        with open(path, 'r') as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(1)
                    continue
                
                for p in patterns:
                    if re.search(p.get("regex", ""), line):
                        await send_ntfy(
                            f"LOG: {p.get('name', 'Pattern Match')}",
                            f"File: {path}\nMatch: {line.strip()}",
                            p.get("priority", "4"),
                            p.get("tags", "mag_right")
                        )
    except Exception as e:
        logger.error(f"Error tailing {path}: {e}")

# --- MAIN LOGIC ---
async def check_critical():
    update_heartbeat()
    stats = get_system_stats()
    issues = []
    limits = config["limits"]

    # Threshold checks
    t = stats["temp"]
    if t:
        if t >= limits["temp"].get("critical", 85): issues.append(f"CRITICAL: CPU Temp {t}C")
        elif t >= limits["temp"].get("warning", 75): issues.append(f"WARNING: CPU Temp {t}C")
    
    r = stats["ram"]
    if r:
        if r >= limits["ram"].get("critical", 90): issues.append(f"CRITICAL: RAM Usage {r}%")
        elif r >= limits["ram"].get("warning", 80): issues.append(f"WARNING: RAM Usage {r}%")
    
    s = stats["swap"]
    if s and s >= limits.get("swap", 80): issues.append(f"High Swap: {s}%")
    
    n = stats["net"]
    if n and n >= limits.get("net_mbps", 100): issues.append(f"High Network: {n} Mbps")

    if issues:
        priority = "5" if any("CRITICAL" in i for i in issues) else "4"
        await send_ntfy("SYSTEM ALERT", "\n".join(issues), priority, "fire,warning")
    else:
        logger.info(f"Health OK: T:{stats['temp']}C | CPU:{stats['cpu']}% | RAM:{stats['ram']}% | Net:{stats['net']}Mbps")

async def send_report(report_type: str = "Daily"):
    stats = get_system_stats()
    lines = [f"Status: Operational"]
    try:
        u = os.uname()
        lines.append(f"OS: {u.sysname} {u.release}")
    except: pass

    if stats["uptime"]:
        d, h = int(stats["uptime"] // 86400), int((stats["uptime"] % 86400) // 3600)
        lines.append(f"Uptime: {d}d {h}h")

    for k in ["temp", "cpu", "ram", "swap", "net"]:
        if stats[k] is not None:
            unit = "C" if k=="temp" else "%" if k in ["cpu","ram","swap"] else " Mbps"
            lines.append(f"{k.capitalize()}: {stats[k]}{unit}")
    
    if current_disk_stats:
        lines.append("\nDisks:")
        for p, u in current_disk_stats.items():
            lines.append(f"- {p}: {u}% (I: {current_inode_stats.get(p,0)}%)")
    
    c_stats = await get_container_stats()
    if c_stats:
        lines.append("\nContainers:")
        lines.extend(c_stats)

    actions = [{"action": "view", "label": "Web Interface", "url": f"http://{config['monitoring']['hostname']}", "clear": True}]
    await send_ntfy(f"{report_type} Status", "\n".join(lines), "3", "calendar", actions=actions)

async def main():
    global daily_report_sent_date
    logger.info(f"--- Server Monitor Starting on {config['monitoring']['hostname']} ---")
    
    await check_disks()
    
    # Background tasks
    tasks = []
    tasks.append(asyncio.create_task(monitor_docker_events()))
    for log_cfg in config["logs"].get("watch_files", []):
        tasks.append(asyncio.create_task(tail_log(log_cfg)))

    last_disk_check = 0
    last_service_check = 0
    
    try:
        while True:
            try:
                now = datetime.now()
                # 1. Critical stats
                await check_critical()
                
                # 2. Daily report
                report_time = config["monitoring"]["daily_time"]
                today = now.strftime("%Y-%m-%d")
                if now.strftime("%H:%M") == report_time and daily_report_sent_date != today:
                    await send_report("Daily")
                    daily_report_sent_date = today
                
                # 3. Periodical checks
                if time.time() - last_disk_check > 3600:
                    await check_disks()
                    await check_backups()
                    last_disk_check = time.time()
                
                if time.time() - last_service_check > 300: # Every 5 mins
                    await check_services()
                    last_service_check = time.time()

            except Exception as e:
                logger.critical(f"Main loop error: {e}")
            
            await asyncio.sleep(config["monitoring"]["check_interval"])
    finally:
        logger.info("Shutting down...")
        for task in tasks:
            task.cancel()
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    logger.error(f"Task finished with error: {res}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
