from __future__ import annotations

import json
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

APP_NAME = "TrueNAS Hardware Monitor"
SYS_ROOT = Path(os.getenv("HOST_SYS", "/host/sys"))
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "/config/config.json"))
POLL_INTERVAL = max(1.0, float(os.getenv("POLL_INTERVAL", "2")))
HISTORY_MINUTES = max(1, int(os.getenv("HISTORY_MINUTES", "60")))
MAX_POINTS = max(30, int(HISTORY_MINUTES * 60 / POLL_INTERVAL))

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
        "fan8": "Unknown fan 8"
    },
    "physical_headers": [
        "CPU_FAN1",
        "CPU_FAN2/WP",
        "CHA_FAN1/WP",
        "CHA_FAN2/WP",
        "CHA_FAN3/WP",
        "CHA_FAN4/WP",
        "CHA_FAN5/WP",
        "CHA_FAN6/WP"
    ],
    "temperature_warn_c": 70,
    "temperature_critical_c": 85,
    "fan_min_rpm": 250
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
    label = read_text(base / f"{prefix}{idx}_label")
    return safe_name(label, fallback)


def read_temperatures(base: Path, chip: str) -> list[dict[str, Any]]:
    sensors: list[dict[str, Any]] = []
    for p in sorted(base.glob("temp*_input"), key=lambda x: int(re.search(r"temp(\d+)_", x.name).group(1)) if re.search(r"temp(\d+)_", x.name) else 999):
        m = re.match(r"temp(\d+)_input", p.name)
        if not m:
            continue
        idx = int(m.group(1))
        raw = read_int(p)
        if raw is None:
            continue
        celsius = raw / 1000.0
        fallback = f"Temp {idx}"
        label = sensor_label(base, "temp", idx, fallback)
        sensors.append({
            "id": f"{chip}:temp{idx}",
            "chip": chip,
            "index": idx,
            "label": label,
            "celsius": round(celsius, 1),
            "max_c": (read_int(base / f"temp{idx}_max") or 0) / 1000.0 or None,
            "crit_c": (read_int(base / f"temp{idx}_crit") or 0) / 1000.0 or None,
        })
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

        fans.append({
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
        })
    return fans


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

    cpu_hottest = max((x["celsius"] for x in cpu_temps), default=None)
    motherboard_hottest = max((x["celsius"] for x in motherboard_temps), default=None)

    return {
        "timestamp": time.time(),
        "system_name": cfg.get("system_name", "TrueNAS"),
        "motherboard_name": cfg.get("motherboard_name", "Motherboard"),
        "thresholds": {
            "warn_c": cfg.get("temperature_warn_c", 70),
            "critical_c": cfg.get("temperature_critical_c", 85),
        },
        "cpu": {"temperatures": cpu_temps, "hottest_c": cpu_hottest},
        "motherboard": {"temperatures": motherboard_temps, "hottest_c": motherboard_hottest},
        "fans": fans,
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
        try:
            snap = read_all_sensors()
            history.append_snapshot(snap)
            with latest_lock:
                latest_snapshot = snap
        except Exception as exc:  # defensive: dashboard should stay alive
            with latest_lock:
                latest_snapshot = {"timestamp": time.time(), "error": str(exc)}
        time.sleep(POLL_INTERVAL)


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
    return HTMLResponse(html_path.read_text())


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
    return {"ok": True, "sys_root": str(SYS_ROOT), "hwmon_devices": discover_hwmon()}
