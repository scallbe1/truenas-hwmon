# TrueNAS Hardware Monitor

A lightweight, read-only live hardware and resource dashboard for TrueNAS SCALE/Linux.

It was designed around an ASRock Z590 Taichi / Nuvoton NCT6686D system with an NVIDIA RTX GPU, but the Linux sensor discovery is generic.

## Live dashboard

The dashboard refreshes once per second by default and shows:

- CPU utilization, load average, package/core temperatures
- Ordinary system RAM used/available/total
- NVIDIA GPU utilization through NVML
- NVIDIA VRAM used/total and percentage
- NVIDIA GPU temperature, power, fan percentage, memory-engine utilization and P-state
- NCT6686D motherboard temperatures
- Fan tachometer RPM and current PWM duty
- Eight configured physical Z590 Taichi fan headers vs. the logical channels exposed by Linux
- Physical disk/NVMe temperatures
- Live read and write throughput per physical disk
- A live right-side process table showing CPU, RAM, GPU VRAM and per-process read/write rates when Linux permits those counters
- 60 minutes of selected in-memory history for sparklines

The app **never writes to PWM controls** and does not create a CUDA compute context. NVIDIA telemetry uses NVML only.

## TrueNAS host prerequisite for Z590 Taichi fan sensors

Load the NCT6686D-compatible driver on the TrueNAS host:

```sh
modprobe nct6683 force=1
```

Verify:

```sh
dmesg | grep -iE 'nct6683|nct6686'
```

For persistence in TrueNAS, add `modprobe nct6683 force=1` as a **Post Init** command in System Settings -> Advanced -> Init/Shutdown Scripts.

## GitHub / GHCR build

The included `.github/workflows/docker-publish.yml` runs the mock host telemetry test and publishes the image to GitHub Container Registry whenever you push to the default branch.

For this repository the image is:

```text
ghcr.io/scallbe1/truenas-hwmon:latest
```

## TrueNAS Custom App

Use the included `truenas-custom-app.yml`. Important host interfaces are mounted read-only:

```yaml
volumes:
  - /sys:/host/sys:ro
  - /proc:/host/proc:ro
  - /mnt/pool1/truenas-hwmon/config:/config:ro
```

The NVIDIA runtime is requested only so NVML can inspect the GPU:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities:
            - gpu

environment:
  NVIDIA_VISIBLE_DEVICES: all
  NVIDIA_DRIVER_CAPABILITIES: utility
```

This does not reserve VRAM or load a model. It allows the monitor to query GPU state while ComfyUI is using the GPU.

Open:

```text
http://TRUENAS-IP:30200
```

## Fan labels

Edit `/mnt/pool1/truenas-hwmon/config/config.json` as physical motherboard headers are identified:

```json
{
  "fan_labels": {
    "fan1": "CPU_FAN1",
    "fan2": "CPU_FAN2/WP",
    "fan3": "CHA_FAN1/WP",
    "fan4": "CHA_FAN2/WP",
    "fan5": "CHA_FAN3/WP",
    "fan6": "CHA_FAN4/WP"
  }
}
```

No image rebuild is needed for label changes because configuration is re-read during polling.

## Security model

- `/sys` and `/proc` are mounted read-only.
- The container runs as UID 10001, not root.
- All Linux capabilities are dropped.
- `no-new-privileges` is enabled.
- The container filesystem is read-only except a small `/tmp` tmpfs.
- No Docker socket is mounted.
- There is no API for writing fan/PWM values.
- Per-process `/proc/<pid>/io` is treated as optional; when Linux does not allow that counter, the UI displays `—` instead of requesting elevated privileges.

## API

- `GET /api/status` - latest full telemetry snapshot
- `GET /api/history` - in-memory history series
- `GET /api/config` - effective configuration
- `GET /health` - service, hwmon and NVIDIA discovery status

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `HOST_SYS` | `/host/sys` | Host sysfs mount |
| `HOST_PROC` | `/host/proc` | Host procfs mount |
| `CONFIG_PATH` | `/config/config.json` | Friendly labels/settings |
| `POLL_INTERVAL` | `1` | Backend polling interval in seconds |
| `HISTORY_MINUTES` | `60` | In-memory history retention |
| `PROCESS_LIMIT` | `18` | Maximum live processes returned |

## Tests

```sh
python3 -m pip install -r requirements.txt
python3 tests/make_mock_hwmon.py
PYTHONPATH=. python3 tests/test_mock.py
```

The mock host includes the Z590 Taichi readings used during development, a physical disk temperature, memory counters, disk I/O, and a changing process sample.
