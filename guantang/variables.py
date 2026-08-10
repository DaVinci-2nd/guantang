import ctypes
import locale
import os
import platform
import subprocess
from datetime import datetime

VARIABLE_LIST = [
    {"keys": ["char", "角色名"], "group": "context", "desc": "当前角色名称"},
    {"keys": ["player", "玩家名"], "group": "context", "desc": "玩家名称"},
    {"keys": ["model_name", "模型名"], "group": "context", "desc": "当前模型标识"},
    {"keys": ["date", "日期"], "group": "datetime", "desc": "当前日期"},
    {"keys": ["time", "时间"], "group": "datetime", "desc": "当前时间"},
    {"keys": ["datetime", "日期时间"], "group": "datetime", "desc": "当前日期和时间"},
    {"keys": ["system", "系统"], "group": "hardware", "desc": "操作系统名称与版本"},
    {"keys": ["arch", "架构"], "group": "hardware", "desc": "CPU 架构"},
    {"keys": ["cpu", "处理器"], "group": "hardware", "desc": "CPU 型号"},
    {"keys": ["cpu_cores", "核心数"], "group": "hardware", "desc": "逻辑处理器数量"},
    {"keys": ["gpu", "显卡"], "group": "hardware", "desc": "显卡型号"},
    {"keys": ["memory", "内存"], "group": "hardware", "desc": "物理内存大小"},
    {"keys": ["hostname", "主机名"], "group": "hardware", "desc": "计算机名称"},
    {"keys": ["language", "语言"], "group": "hardware", "desc": "系统语言"},
]

GROUPS = {"context": "上下文", "datetime": "日期时间", "hardware": "硬件"}

_static_cache = None


def _system_name() -> str:
    system = platform.system()
    release = platform.release()
    if system == "Windows":
        return f"Windows {release}"
    if system == "Darwin":
        return f"macOS {release}"
    return f"{system} {release}"


def _gpu_windows() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        return lines[0] if lines else "未知"
    except Exception:
        return "未知"


def _memory() -> str:
    system = platform.system()
    try:
        if system == "Windows":

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return f"{stat.ullTotalPhys / (1024 ** 3):.0f} GB"
        elif system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return f"{kb / 1024 / 1024:.0f} GB"
    except Exception:
        pass
    return "未知"


def _language() -> str:
    try:
        lang = locale.getdefaultlocale()[0]
        return lang or "未知"
    except Exception:
        return "未知"


def collect_static() -> dict:
    global _static_cache
    if _static_cache is not None:
        return _static_cache
    _static_cache = {
        "system": _system_name(),
        "arch": platform.machine() or "未知",
        "cpu": platform.processor() or "未知",
        "cpu_cores": str(os.cpu_count() or 0),
        "gpu": _gpu_windows() if platform.system() == "Windows" else "未知",
        "memory": _memory(),
        "hostname": platform.node() or "未知",
        "language": _language(),
    }
    return _static_cache


def build_values(model_name: str = "", role_name: str = "", player_name: str = "") -> dict:
    now = datetime.now()
    values = {
        "char": role_name or "",
        "player": player_name or "",
        "model_name": model_name or "",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    values.update(collect_static())
    return values


def render(text: str, values: dict) -> str:
    for item in VARIABLE_LIST:
        value = str(values.get(item["keys"][0], "") or "")
        for alias in item["keys"]:
            text = text.replace("{{" + alias + "}}", value)
    return text
