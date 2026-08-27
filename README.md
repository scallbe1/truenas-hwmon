# TrueNAS Hardware Monitor v2.4

A compact, live hardware/resource dashboard for TrueNAS SCALE/Linux, designed around the ASRock Z590 Taichi / Nuvoton NCT6686D and NVIDIA GPUs.

## Dashboard

The desktop layout is intentionally constrained to one browser screen and refreshes once per second. It shows:

- CPU utilization/load and every exposed package/core temperature
- System RAM used/available/total
- NVIDIA GPU utilization, VRAM, temperature, fan, power, memory-engine utilization and P-state via NVML
- Every NCT6686D motherboard temperature
- Every exposed fan RPM and PWM duty plus the eight configured physical Z590 Taichi headers
- Physical disk/NVMe temperature and live read/write throughput
- A consolidated **All temperatures** matrix so CPU, motherboard, storage and other hwmon temperatures are visible without scrolling
- Right-side independent rankings:
  - Top 5 CPU processes
  - Top 5 network containers/network namespaces
  - Top 5 disk-I/O processes
- Container/app/service identity plus the process inside it when Docker metadata is available
- Linear RAM bars in each Top-5 list, so larger memory users are always represented by proportionally larger bars
- 60 minutes of selected telemetry history in RAM for sparklines

The app never writes fan/PWM controls and NVML monitoring does not allocate model VRAM.

### Network accounting note

Linux `/proc` exposes network byte counters per **network namespace**, not truthful per-process network byte totals. v2.4 therefore ranks network namespaces/containers and shows the busiest process(es) in that namespace for context. It does not falsely assign the entire container's traffic to one process. True per-process network attribution would require a privileged packet/eBPF collector.

## Z590 Taichi sensor prerequisite

```sh
modprobe nct6683 force=1
```

To persist it as a TrueNAS Post Init command:

```sh
midclt call initshutdownscript.create '{
  "type": "COMMAND",
  "command": "modprobe nct6683 force=1",
  "when": "POSTINIT",
  "enabled": true,
  "timeout": 10,
  "comment": "Load Z590 Taichi NCT6686D hwmon driver"
}'
```

## GitHub / GHCR

The included workflow tests the mock telemetry host and publishes:

```text
ghcr.io/scallbe1/truenas-hwmon:latest
```

## TrueNAS Custom App

Use `truenas-custom-app.yml`.

The app mounts host telemetry and Docker metadata read-only:

```yaml
volumes:
  - /sys:/host/sys:ro
  - /proc:/host/proc:ro
  - /mnt/.ix-apps/docker/containers:/host/docker/containers:ro
  - /mnt/pool1/truenas-hwmon/config:/config:ro
```

Docker metadata is used only to translate cgroup container IDs into useful app/container/service names; the Docker socket is **not** mounted.

The process sampler runs as root with only `SYS_PTRACE` added so Linux permits reading process I/O and namespace metadata across differing host/container UIDs. The container filesystem and every host mount remain read-only, all other capabilities are dropped, and `no-new-privileges` remains enabled.

Open:

```text
http://TRUENAS-IP:30200
```

The page is served with `Cache-Control: no-store` and displays **v2.4** beside the title so it is obvious when the new image is actually running.

## Fan labels

Edit `/mnt/pool1/truenas-hwmon/config/config.json` as physical headers are identified. No image rebuild is needed.

## API

- `GET /api/status` — current sensors plus `top_cpu`, `top_network`, `top_disk`
- `GET /api/history` — selected in-memory history
- `GET /api/config` — effective configuration
- `GET /health` — sensor/NVIDIA discovery information

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `HOST_SYS` | `/host/sys` | Host sysfs |
| `HOST_PROC` | `/host/proc` | Host procfs |
| `DOCKER_CONTAINERS_ROOT` | `/host/docker/containers` | Read-only Docker metadata |
| `CONFIG_PATH` | `/config/config.json` | Friendly labels/settings |
| `POLL_INTERVAL` | `1` | Polling interval in seconds |
| `HISTORY_MINUTES` | `60` | In-memory history |
| `PROCESS_LIMIT` | `20` | General process API limit |

## Tests

```sh
python3 -m pip install -r requirements.txt
python3 tests/make_mock_hwmon.py
PYTHONPATH=. python3 tests/test_mock.py
```

The mock verifies NCT6686D sensors, disk temperature/I/O, memory, container-name resolution, per-process CPU/disk I/O and network-namespace throughput.


## v2.4 dashboard changes

- Adds a dedicated Top-5 memory-process panel with linear proportional RSS bars and host-memory percentage.
- CPU, network and disk Top-5 panels no longer repeat memory bars.
- Temperature groups use a denser two-column matrix so all exposed temperatures remain visible with larger default typography.
- The right side is four equal live panels: CPU, Memory, Network and Disk I/O.
