"""Read-only server resource metrics for an authenticated operator collector.

Never publish hostnames, process lists, IP addresses or secrets.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _meminfo():
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        return {line.split(":")[0]: int(line.split(":")[1].strip().split()[0])
                for line in lines if ":" in line}
    except (OSError, ValueError, IndexError):
        return {}


def snapshot(root=None):
    mem = _meminfo()
    total = mem.get("MemTotal", 0)
    available = mem.get("MemAvailable", 0)
    swap = mem.get("SwapTotal", 0)
    free_swap = mem.get("SwapFree", 0)
    try:
        load1 = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        cpu_load_percent = round(100 * load1 / cores, 1)
    except (OSError, ValueError):
        cpu_load_percent = None
    try:
        disk = shutil.disk_usage(root or os.getenv("BOT_DATA_DIR", "/home/ubuntu/chzzk-bot"))
        disk_percent = round(100 * disk.used / disk.total, 1)
    except (OSError, ValueError, ZeroDivisionError):
        disk_percent = None
    return {
        "collected_at": datetime.now(KST).isoformat(),
        "cpu_load_1m_per_core_percent": cpu_load_percent,
        "memory_percent": round(100 * (total - available) / total, 1) if total else None,
        "swap_percent": round(100 * (swap - free_swap) / swap, 1) if swap else None,
        "disk_percent": disk_percent,
    }


if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False))
