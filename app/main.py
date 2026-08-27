from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

try:
    import pynvml  # type: ignore
except Exception:  # pragma: no cover - optional on non-NVIDIA/test hosts
    pynvml = None

APP_NAME = "TrueNAS Hardware Monitor"
SYS_ROOT = Path(os.getenv("HOST_SYS", "/host/sys"))
PROC_ROOT = Path(os.getenv("HOST_PROC", "/host/proc"))
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.json"))
DOCKER_CONTAINERS_ROOT = Path(os.getenv("DOCKER_CONTAINERS_ROOT", "/host/docker/containers"))
POLL_INTERVAL = max(0.5, float(os.getenv("POLL_INTERVAL", "1")))
HISTORY_MINUTES = max(1, int(os.getenv("HISTORY_MINUTES", "60")))
PROCESS_LIMIT = max(5, min(50, int(os.getenv("PROCESS_LIMIT", "18"))))
MAX_POINTS = max(60, int(HISTORY_MINUTES * 60 / POLL_INTERVAL))

DEFAULT_CONFIG: dict[str, Any] = {
    "system_name": "TrueNAS",
    "motherboard_name": "ASRock Z590 Taichi",
    "fan_labels": {
        "fan1": "Unknown fan 1",
        "fan2": "Unknown fan 2",
        "fan3": "Unknown fan 3",
        "fan4": "Unknown fan 4",
        "fan5": "Unknown fan 5",
        "fan6": "Unknown fan 6",
        "fan7": "Unknown fan 7",
        "fan8": "Unknown fan 8",
    },
    "physical_headers": [
        "CPU_FAN1",
        "CPU_FAN2/WP",
        "CHA_FAN1/WP",
        "CHA_FAN2/WP",
        "CHA_FAN3/WP",
        "CHA_FAN4/WP",
        "CHA_FAN5/WP",
        "CHA_FAN6/WP",
    ],
    "temperature_warn_c": 70,
    "temperature_critical_c": 85,
    "fan_min_rpm": 250,
}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def read_int(path: Path) -> int | None:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def safe_name(value: str | None, fallback: str) -> str:
    value = (value or "").strip()
    return value if value else fallback


def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user_cfg = json.loads(CONFIG_PATH.read_text())
        if isinstance(user_cfg, dict):
            for key, value in user_cfg.items():
                if key == "fan_labels" and isinstance(value, dict):
                    cfg["fan_labels"].update({str(k): str(v) for k, v in value.items()})
                else:
                    cfg[key] = value
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def hwmon_dirs() -> list[Path]:
    base = SYS_ROOT / "class" / "hwmon"
    try:
        return sorted(base.glob("hwmon*"), key=lambda p: p.name)
    except OSError:
        return []


def discover_hwmon() -> list[dict[str, str]]:
    out = []
    for d in hwmon_dirs():
        out.append({"path": str(d), "name": safe_name(read_text(d / "name"), d.name)})
    return out


def sensor_label(base: Path, prefix: str, idx: int, fallback: str) -> str:
    return safe_name(read_text(base / f"{prefix}{idx}_label"), fallback)


def hwmon_block_identity(base: Path) -> tuple[str | None, str | None]:
    """Return (block_device, nvme_controller) inferred from the hwmon sysfs path."""
    try:
        resolved = str(base.resolve())
    except OSError:
        resolved = str(base)
    m = re.search(r"/block/([^/]+)", resolved)
    block = m.group(1) if m else None
    m = re.search(r"/nvme/(nvme\d+)(?:/|$)", resolved)
    controller = m.group(1) if m else None
    return block, controller


def read_temperatures(base: Path, chip: str) -> list[dict[str, Any]]:
    sensors: list[dict[str, Any]] = []
    block_device, nvme_controller = hwmon_block_identity(base)
    paths = sorted(
        base.glob("temp*_input"),
        key=lambda x: int(re.search(r"temp(\d+)_", x.name).group(1)) if re.search(r"temp(\d+)_", x.name) else 999,
    )
    for p in paths:
        m = re.match(r"temp(\d+)_input", p.name)
        if not m:
            continue
        idx = int(m.group(1))
        raw = read_int(p)
        if raw is None:
            continue
        celsius = raw / 1000.0
        sensors.append(
            {
                "id": f"{chip}:temp{idx}:{base.name}",
                "chip": chip,
                "index": idx,
                "label": sensor_label(base, "temp", idx, f"Temp {idx}"),
                "celsius": round(celsius, 1),
                "max_c": (read_int(base / f"temp{idx}_max") or 0) / 1000.0 or None,
                "crit_c": (read_int(base / f"temp{idx}_crit") or 0) / 1000.0 or None,
                "block_device": block_device,
                "nvme_controller": nvme_controller,
            }
        )
    return sensors


def read_fans(base: Path, chip: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    fans: list[dict[str, Any]] = []
    indices: set[int] = set()
    for p in base.glob("fan*_input"):
        m = re.match(r"fan(\d+)_input", p.name)
        if m:
            indices.add(int(m.group(1)))
    for p in base.glob("pwm[0-9]*"):
        m = re.fullmatch(r"pwm(\d+)", p.name)
        if m:
            indices.add(int(m.group(1)))

    for idx in sorted(indices):
        fan_key = f"fan{idx}"
        rpm = read_int(base / f"fan{idx}_input")
        pwm = read_int(base / f"pwm{idx}")
        pwm_percent = round((pwm or 0) * 100 / 255) if pwm is not None else None
        kernel_label = read_text(base / f"fan{idx}_label")
        configured_label = cfg.get("fan_labels", {}).get(fan_key)
        label = safe_name(configured_label or kernel_label, fan_key)
        pwm_path = base / f"pwm{idx}"
        writable = os.access(pwm_path, os.W_OK) if pwm_path.exists() else False

        if rpm is None:
            state = "unavailable"
        elif rpm == 0 and (pwm is None or pwm == 0):
            state = "stopped-or-unused"
        elif rpm == 0:
            state = "stopped"
        elif rpm < int(cfg.get("fan_min_rpm", 250)):
            state = "low"
        else:
            state = "running"

        fans.append(
            {
                "id": f"{chip}:fan{idx}",
                "chip": chip,
                "index": idx,
                "kernel_name": fan_key,
                "label": label,
                "rpm": rpm,
                "pwm": pwm,
                "pwm_percent": pwm_percent,
                "pwm_writable": writable,
                "state": state,
            }
        )
    return fans


def read_meminfo() -> dict[str, Any]:
    values: dict[str, int] = {}
    raw = read_text(PROC_ROOT / "meminfo") or ""
    for line in raw.splitlines():
        m = re.match(r"([^:]+):\s+(\d+)", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": round(used * 100 / total, 1) if total else None,
        "cached_bytes": values.get("Cached", 0),
        "buffers_bytes": values.get("Buffers", 0),
    }


def read_loadavg() -> dict[str, float | None]:
    raw = read_text(PROC_ROOT / "loadavg")
    if not raw:
        return {"load1": None, "load5": None, "load15": None}
    parts = raw.split()
    try:
        return {"load1": float(parts[0]), "load5": float(parts[1]), "load15": float(parts[2])}
    except (ValueError, IndexError):
        return {"load1": None, "load5": None, "load15": None}


def read_cpu_times() -> tuple[int, int] | None:
    raw = read_text(PROC_ROOT / "stat") or ""
    first = raw.splitlines()[0] if raw else ""
    if not first.startswith("cpu "):
        return None
    try:
        vals = [int(x) for x in first.split()[1:]]
    except ValueError:
        return None
    total = sum(vals)
    idle = (vals[3] if len(vals) > 3 else 0) + (vals[4] if len(vals) > 4 else 0)
    return total, idle


def block_model(name: str) -> str:
    model = read_text(SYS_ROOT / "block" / name / "device" / "model")
    vendor = read_text(SYS_ROOT / "block" / name / "device" / "vendor")
    value = " ".join(x.strip() for x in (vendor, model) if x and x.strip())
    return value or name


def physical_block_devices() -> list[str]:
    base = SYS_ROOT / "block"
    names: list[str] = []
    try:
        for p in base.iterdir():
            n = p.name
            if re.fullmatch(r"(?:sd[a-z]+|hd[a-z]+|nvme\d+n\d+)", n):
                names.append(n)
    except OSError:
        pass
    return sorted(names)


def read_diskstats_raw() -> dict[str, tuple[int, int]]:
    wanted = set(physical_block_devices())
    result: dict[str, tuple[int, int]] = {}
    raw = read_text(PROC_ROOT / "diskstats") or ""
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        if name not in wanted:
            continue
        try:
            sectors_read = int(parts[5])
            sectors_written = int(parts[9])
        except (ValueError, IndexError):
            continue
        result[name] = (sectors_read, sectors_written)
    return result


def extract_container_id(cgroup_text: str) -> str | None:
    """Extract a Docker-style container ID from a process cgroup path."""
    matches = re.findall(r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])", cgroup_text.lower())
    return matches[-1] if matches else None


def read_netns_id(pid_path: Path) -> str | None:
    try:
        target = os.readlink(pid_path / "ns" / "net")
    except OSError:
        return None
    m = re.match(r"net:\[(\d+)\]", target)
    return m.group(1) if m else target


def read_netdev_bytes(pid_path: Path) -> tuple[int, int] | None:
    """Read aggregate RX/TX byte counters for a process network namespace."""
    raw = read_text(pid_path / "net" / "dev")
    if not raw:
        return None
    rx = tx = 0
    for line in raw.splitlines():
        if ":" not in line:
            continue
        iface, payload = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        parts = payload.split()
        if len(parts) < 9:
            continue
        try:
            rx += int(parts[0])
            tx += int(parts[8])
        except ValueError:
            continue
    return rx, tx


class ContainerResolver:
    """Resolve Docker container IDs to human-friendly names using read-only metadata."""

    def __init__(self) -> None:
        self._last_refresh = 0.0
        self._items: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh < 15:
            return
        items: dict[str, dict[str, str]] = {}
        try:
            dirs = list(DOCKER_CONTAINERS_ROOT.iterdir())
        except OSError:
            dirs = []
        for d in dirs:
            if not d.is_dir():
                continue
            cid = d.name.lower()
            try:
                data = json.loads((d / "config.v2.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            labels = ((data.get("Config") or {}).get("Labels") or {}) if isinstance(data, dict) else {}
            raw_name = str(data.get("Name") or "").lstrip("/")
            project = str(labels.get("com.docker.compose.project") or "")
            service = str(labels.get("com.docker.compose.service") or "")
            app_name = project[3:] if project.startswith("ix-") else project
            items[cid] = {
                "container_name": raw_name or cid[:12],
                "app_name": app_name or raw_name or cid[:12],
                "service_name": service,
                "image": str((data.get("Config") or {}).get("Image") or ""),
            }
        with self._lock:
            self._items = items
            self._last_refresh = now

    def resolve(self, container_id: str | None) -> dict[str, str]:
        if not container_id:
            return {"container_name": "host", "app_name": "host", "service_name": "", "image": ""}
        self._refresh()
        with self._lock:
            item = self._items.get(container_id)
            if item:
                return dict(item)
            # Be tolerant of runtimes exposing shortened IDs in cgroup paths.
            for cid, candidate in self._items.items():
                if cid.startswith(container_id) or container_id.startswith(cid):
                    return dict(candidate)
        return {"container_name": container_id[:12], "app_name": container_id[:12], "service_name": "", "image": ""}


container_resolver = ContainerResolver()


def read_process_raw() -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:
        return out
    for p in entries:
        if not p.name.isdigit():
            continue
        pid = int(p.name)
        stat = read_text(p / "stat")
        if not stat:
            continue
        close = stat.rfind(")")
        open_ = stat.find("(")
        if open_ < 0 or close < 0:
            continue
        comm = stat[open_ + 1 : close]
        rest = stat[close + 2 :].split()
        try:
            ticks = int(rest[11]) + int(rest[12])
        except (ValueError, IndexError):
            continue
        rss_bytes = 0
        statm = read_text(p / "statm")
        if statm:
            try:
                rss_bytes = int(statm.split()[1]) * os.sysconf("SC_PAGE_SIZE")
            except (ValueError, IndexError, OSError):
                pass
        cmd_raw = b""
        try:
            cmd_raw = (p / "cmdline").read_bytes()[:4096]
        except OSError:
            pass
        command = cmd_raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip() or comm
        read_bytes = write_bytes = None
        io_raw = read_text(p / "io")
        if io_raw:
            for line in io_raw.splitlines():
                if line.startswith("read_bytes:"):
                    try:
                        read_bytes = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("write_bytes:"):
                    try:
                        write_bytes = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
        cgroup_text = read_text(p / "cgroup") or ""
        container_id = extract_container_id(cgroup_text)
        netns_id = read_netns_id(p)
        out[pid] = {
            "pid": pid,
            "comm": comm,
            "command": command,
            "ticks": ticks,
            "rss_bytes": rss_bytes,
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
            "container_id": container_id,
            "netns_id": netns_id,
        }
    return out


class NvidiaMonitor:
    def __init__(self) -> None:
        self.initialized = False
        self.error: str | None = None
        self._init_lock = threading.Lock()

    def _ensure(self) -> bool:
        if self.initialized:
            return True
        if pynvml is None:
            self.error = "nvidia-ml-py is unavailable"
            return False
        with self._init_lock:
            if self.initialized:
                return True
            try:
                pynvml.nvmlInit()
                self.initialized = True
                self.error = None
                return True
            except Exception as exc:  # pragma: no cover - hardware dependent
                self.error = str(exc)
                return False

    def snapshot(self) -> tuple[dict[str, Any], dict[int, int]]:
        if not self._ensure():
            return {"available": False, "error": self.error, "gpus": []}, {}
        gpus: list[dict[str, Any]] = []
        process_vram: dict[int, int] = {}
        try:
            count = pynvml.nvmlDeviceGetCount()
            for idx in range(count):
                h = pynvml.nvmlDeviceGetHandleByIndex(idx)
                name = pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                util = pynvml.nvmlDeviceGetUtilizationRates(h)
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
                try:
                    power_w = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
                except Exception:
                    power_w = None
                try:
                    fan_pct = pynvml.nvmlDeviceGetFanSpeed(h)
                except Exception:
                    fan_pct = None
                try:
                    pstate = int(pynvml.nvmlDeviceGetPerformanceState(h))
                except Exception:
                    pstate = None
                gpu_processes: dict[int, int] = {}
                for getter_name in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
                    getter = getattr(pynvml, getter_name, None)
                    if not getter:
                        continue
                    try:
                        for proc in getter(h):
                            used = int(getattr(proc, "usedGpuMemory", 0) or 0)
                            if used > (1 << 60):  # NVML_VALUE_NOT_AVAILABLE sentinel on some bindings
                                used = 0
                            gpu_processes[int(proc.pid)] = max(gpu_processes.get(int(proc.pid), 0), used)
                    except Exception:
                        pass
                for pid, used in gpu_processes.items():
                    process_vram[pid] = process_vram.get(pid, 0) + used
                gpus.append(
                    {
                        "index": idx,
                        "name": str(name),
                        "util_percent": int(util.gpu),
                        "memory_util_percent": int(util.memory),
                        "vram_used_bytes": int(mem.used),
                        "vram_total_bytes": int(mem.total),
                        "vram_percent": round(mem.used * 100 / mem.total, 1) if mem.total else None,
                        "temperature_c": int(temp),
                        "power_w": round(power_w, 1) if power_w is not None else None,
                        "fan_percent": fan_pct,
                        "pstate": pstate,
                    }
                )
            return {"available": True, "error": None, "gpus": gpus}, process_vram
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.error = str(exc)
            return {"available": False, "error": self.error, "gpus": []}, {}


class RuntimeSampler:
    def __init__(self) -> None:
        self.prev_ts: float | None = None
        self.prev_cpu: tuple[int, int] | None = None
        self.prev_disks: dict[str, tuple[int, int]] = {}
        self.prev_processes: dict[int, dict[str, Any]] = {}
        self.prev_network: dict[str, tuple[int, int]] = {}
        try:
            self.clk_tck = int(os.sysconf("SC_CLK_TCK"))
        except (ValueError, OSError):
            self.clk_tck = 100

    def sample(self, total_memory: int, gpu_process_vram: dict[int, int]) -> dict[str, Any]:
        now = time.time()
        dt = max(0.001, now - self.prev_ts) if self.prev_ts is not None else None

        current_cpu = read_cpu_times()
        cpu_percent = None
        if dt is not None and current_cpu is not None and self.prev_cpu is not None:
            total_delta = current_cpu[0] - self.prev_cpu[0]
            idle_delta = current_cpu[1] - self.prev_cpu[1]
            if total_delta > 0:
                cpu_percent = round((1.0 - idle_delta / total_delta) * 100, 1)

        current_disks = read_diskstats_raw()
        disks: list[dict[str, Any]] = []
        for name in physical_block_devices():
            cur = current_disks.get(name)
            prev = self.prev_disks.get(name)
            read_mbps = write_mbps = 0.0
            if dt is not None and cur and prev:
                read_mbps = max(0.0, (cur[0] - prev[0]) * 512 / dt / 1_000_000)
                write_mbps = max(0.0, (cur[1] - prev[1]) * 512 / dt / 1_000_000)
            disks.append(
                {
                    "name": name,
                    "model": block_model(name),
                    "read_mbps": round(read_mbps, 2),
                    "write_mbps": round(write_mbps, 2),
                }
            )

        current_processes = read_process_raw()

        # Sample each unique network namespace once. Linux exposes namespace totals,
        # not honest per-process byte counters, so network ranking is container/netns scoped.
        net_representatives: dict[str, int] = {}
        for pid, cur in current_processes.items():
            ns = cur.get("netns_id")
            if ns and ns not in net_representatives:
                net_representatives[ns] = pid
        current_network: dict[str, tuple[int, int]] = {}
        for ns, pid in net_representatives.items():
            counters = read_netdev_bytes(PROC_ROOT / str(pid))
            if counters is not None:
                current_network[ns] = counters

        processes: list[dict[str, Any]] = []
        processes_by_ns: dict[str, list[dict[str, Any]]] = {}
        for pid, cur in current_processes.items():
            prev = self.prev_processes.get(pid)
            proc_cpu = 0.0
            read_mbps = write_mbps = None
            if dt is not None and prev:
                tick_delta = cur["ticks"] - prev["ticks"]
                if tick_delta >= 0:
                    proc_cpu = max(0.0, tick_delta / self.clk_tck / dt * 100)
                if cur["read_bytes"] is not None and prev["read_bytes"] is not None:
                    read_mbps = max(0.0, (cur["read_bytes"] - prev["read_bytes"]) / dt / 1_000_000)
                if cur["write_bytes"] is not None and prev["write_bytes"] is not None:
                    write_mbps = max(0.0, (cur["write_bytes"] - prev["write_bytes"]) / dt / 1_000_000)
            mem_pct = cur["rss_bytes"] * 100 / total_memory if total_memory else 0.0
            gpu_vram = gpu_process_vram.get(pid, 0)
            container = container_resolver.resolve(cur.get("container_id"))
            item = {
                "pid": pid,
                "name": cur["comm"],
                "command": cur["command"],
                "cpu_percent": round(proc_cpu, 1),
                "rss_bytes": cur["rss_bytes"],
                "memory_percent": round(mem_pct, 2),
                "read_mbps": round(read_mbps, 2) if read_mbps is not None else None,
                "write_mbps": round(write_mbps, 2) if write_mbps is not None else None,
                "disk_mbps": round((read_mbps or 0.0) + (write_mbps or 0.0), 2),
                "gpu_vram_bytes": gpu_vram,
                "container_id": cur.get("container_id"),
                "container_name": container["container_name"],
                "app_name": container["app_name"],
                "service_name": container["service_name"],
                "netns_id": cur.get("netns_id"),
            }
            processes.append(item)
            if cur.get("netns_id"):
                processes_by_ns.setdefault(str(cur["netns_id"]), []).append(item)

        top_cpu = sorted(processes, key=lambda x: (x["cpu_percent"], x["rss_bytes"]), reverse=True)[:5]
        top_memory = sorted(processes, key=lambda x: (x["rss_bytes"], x["cpu_percent"]), reverse=True)[:5]
        top_disk = sorted(processes, key=lambda x: (x["disk_mbps"], x["cpu_percent"]), reverse=True)[:5]

        network_groups: list[dict[str, Any]] = []
        if dt is not None:
            for ns, cur_counter in current_network.items():
                prev_counter = self.prev_network.get(ns)
                if not prev_counter:
                    continue
                rx_mbps = max(0.0, (cur_counter[0] - prev_counter[0]) / dt / 1_000_000)
                tx_mbps = max(0.0, (cur_counter[1] - prev_counter[1]) / dt / 1_000_000)
                members = processes_by_ns.get(ns, [])
                if not members:
                    continue
                members_sorted = sorted(members, key=lambda x: (x["cpu_percent"], x["rss_bytes"]), reverse=True)
                primary = members_sorted[0]
                network_groups.append({
                    "netns_id": ns,
                    "container_name": primary["container_name"],
                    "app_name": primary["app_name"],
                    "service_name": primary["service_name"],
                    "rx_mbps": round(rx_mbps, 2),
                    "tx_mbps": round(tx_mbps, 2),
                    "total_mbps": round(rx_mbps + tx_mbps, 2),
                    "rss_bytes": sum(int(x["rss_bytes"]) for x in members),
                    "process_count": len(members),
                    "processes": [
                        {"pid": x["pid"], "name": x["name"], "command": x["command"], "cpu_percent": x["cpu_percent"]}
                        for x in members_sorted[:3]
                    ],
                })
        top_network = sorted(network_groups, key=lambda x: x["total_mbps"], reverse=True)[:5]

        # Retain the old general list for API compatibility, but rank it sanely by CPU then RAM.
        processes_sorted = sorted(processes, key=lambda x: (x["cpu_percent"], x["rss_bytes"]), reverse=True)

        self.prev_ts = now
        self.prev_cpu = current_cpu
        self.prev_disks = current_disks
        self.prev_processes = current_processes
        self.prev_network = current_network

        return {
            "cpu_percent": cpu_percent,
            "load": read_loadavg(),
            "disks": disks,
            "processes": processes_sorted[:PROCESS_LIMIT],
            "top_cpu": top_cpu,
            "top_memory": top_memory,
            "top_disk": top_disk,
            "top_network": top_network,
        }


def attach_disk_temperatures(disks: list[dict[str, Any]], temps: list[dict[str, Any]]) -> None:
    by_block: dict[str, float] = {}
    by_nvme_controller: dict[str, float] = {}
    for t in temps:
        block = t.get("block_device")
        controller = t.get("nvme_controller")
        c = t.get("celsius")
        if c is None:
            continue
        if block:
            by_block[block] = max(by_block.get(block, -math.inf), float(c))
        if controller:
            by_nvme_controller[controller] = max(by_nvme_controller.get(controller, -math.inf), float(c))
    for disk in disks:
        temp = by_block.get(disk["name"])
        if temp is None:
            m = re.match(r"(nvme\d+)n\d+", disk["name"])
            if m:
                temp = by_nvme_controller.get(m.group(1))
        disk["temperature_c"] = round(temp, 1) if temp is not None else None


nvidia_monitor = NvidiaMonitor()
runtime_sampler = RuntimeSampler()


def read_all_sensors() -> dict[str, Any]:
    cfg = load_config()
    devices = []
    cpu_temps: list[dict[str, Any]] = []
    motherboard_temps: list[dict[str, Any]] = []
    other_temps: list[dict[str, Any]] = []
    fans: list[dict[str, Any]] = []

    for d in hwmon_dirs():
        chip = safe_name(read_text(d / "name"), d.name)
        temps = read_temperatures(d, chip)
        chip_fans = read_fans(d, chip, cfg)
        devices.append({"name": chip, "path": str(d), "temperatures": len(temps), "fans": len(chip_fans)})

        if chip == "coretemp" or chip.startswith("k10temp") or chip.startswith("zenpower"):
            cpu_temps.extend(temps)
        elif chip.startswith("nct668"):
            motherboard_temps.extend(temps)
            fans.extend(chip_fans)
        else:
            other_temps.extend(temps)
            fans.extend(chip_fans)

    memory = read_meminfo()
    gpu, gpu_process_vram = nvidia_monitor.snapshot()
    runtime = runtime_sampler.sample(memory.get("total_bytes", 0), gpu_process_vram)
    attach_disk_temperatures(runtime["disks"], other_temps)

    cpu_hottest = max((x["celsius"] for x in cpu_temps), default=None)
    motherboard_hottest = max((x["celsius"] for x in motherboard_temps), default=None)

    return {
        "timestamp": time.time(),
        "poll_interval": POLL_INTERVAL,
        "system_name": cfg.get("system_name", "TrueNAS"),
        "motherboard_name": cfg.get("motherboard_name", "Motherboard"),
        "thresholds": {
            "warn_c": cfg.get("temperature_warn_c", 70),
            "critical_c": cfg.get("temperature_critical_c", 85),
        },
        "cpu": {
            "temperatures": cpu_temps,
            "hottest_c": cpu_hottest,
            "usage_percent": runtime["cpu_percent"],
            "load": runtime["load"],
        },
        "memory": memory,
        "gpu": gpu,
        "motherboard": {"temperatures": motherboard_temps, "hottest_c": motherboard_hottest},
        "fans": fans,
        "disks": runtime["disks"],
        "processes": runtime["processes"],
        "top_cpu": runtime["top_cpu"],
        "top_memory": runtime["top_memory"],
        "top_disk": runtime["top_disk"],
        "top_network": runtime["top_network"],
        "other_temperatures": other_temps,
        "devices": devices,
        "physical_headers": cfg.get("physical_headers", []),
    }


class History:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict[str, deque[tuple[float, float]]] = {}

    def append_snapshot(self, snap: dict[str, Any]) -> None:
        ts = float(snap["timestamp"])
        values: dict[str, float] = {}
        for group in (snap["cpu"]["temperatures"], snap["motherboard"]["temperatures"], snap["other_temperatures"]):
            for s in group:
                values[s["id"]] = float(s["celsius"])
        for fan in snap["fans"]:
            if fan["rpm"] is not None:
                values[fan["id"]] = float(fan["rpm"])
        if snap["cpu"].get("usage_percent") is not None:
            values["system:cpu_percent"] = float(snap["cpu"]["usage_percent"])
        if snap["memory"].get("used_percent") is not None:
            values["system:memory_percent"] = float(snap["memory"]["used_percent"])
        for gpu in snap.get("gpu", {}).get("gpus", []):
            idx = gpu["index"]
            values[f"gpu:{idx}:util"] = float(gpu["util_percent"])
            values[f"gpu:{idx}:vram"] = float(gpu["vram_percent"] or 0)
            values[f"gpu:{idx}:temp"] = float(gpu["temperature_c"])
        for disk in snap.get("disks", []):
            name = disk["name"]
            values[f"disk:{name}:read"] = float(disk["read_mbps"])
            values[f"disk:{name}:write"] = float(disk["write_mbps"])
            if disk.get("temperature_c") is not None:
                values[f"disk:{name}:temp"] = float(disk["temperature_c"])

        with self._lock:
            for key, value in values.items():
                self._series.setdefault(key, deque(maxlen=MAX_POINTS)).append((ts, value))

    def export(self) -> dict[str, list[list[float]]]:
        with self._lock:
            return {k: [[ts, value] for ts, value in series] for k, series in self._series.items()}


history = History()
latest_lock = threading.Lock()
latest_snapshot: dict[str, Any] = {}


def poller() -> None:
    global latest_snapshot
    while True:
        started = time.monotonic()
        try:
            snap = read_all_sensors()
            history.append_snapshot(snap)
            with latest_lock:
                latest_snapshot = snap
        except Exception as exc:  # defensive: dashboard should stay alive
            with latest_lock:
                latest_snapshot = {"timestamp": time.time(), "error": str(exc)}
        elapsed = time.monotonic() - started
        time.sleep(max(0.05, POLL_INTERVAL - elapsed))


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.on_event("startup")
def startup() -> None:
    global latest_snapshot
    latest_snapshot = read_all_sensors()
    history.append_snapshot(latest_snapshot)
    thread = threading.Thread(target=poller, daemon=True, name="hwmon-poller")
    thread.start()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(), headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    with latest_lock:
        return dict(latest_snapshot)


@app.get("/api/history")
def api_history() -> dict[str, Any]:
    return {"history_minutes": HISTORY_MINUTES, "series": history.export()}


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return load_config()


@app.get("/health")
def health() -> dict[str, Any]:
    with latest_lock:
        gpu_ok = bool(latest_snapshot.get("gpu", {}).get("available")) if latest_snapshot else False
    return {
        "ok": True,
        "sys_root": str(SYS_ROOT),
        "proc_root": str(PROC_ROOT),
        "docker_containers_root": str(DOCKER_CONTAINERS_ROOT),
        "hwmon_devices": discover_hwmon(),
        "nvidia_available": gpu_ok,
    }
