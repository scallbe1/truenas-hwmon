# TrueNAS Hardware Monitor

A small, read-only web dashboard for TrueNAS SCALE/Linux hardware-monitoring sensors exposed through `/sys/class/hwmon`.

Designed around an ASRock Z590 Taichi / Nuvoton NCT6686D system, but discovery is generic and works with normal Linux `hwmon` devices such as `coretemp`, `drivetemp`, `nvme`, and Nuvoton monitor chips.

## What it shows

- CPU package/core temperatures exposed by `coretemp`
- NCT6686D motherboard temperatures
- Fan tachometer RPM
- Current PWM duty as both 0-255 and percentage
- Whether a PWM sysfs node is writable
- Other `hwmon` temperatures such as drive/NVMe temperatures
- 60 minutes of in-memory history (configurable)
- Configurable friendly labels for `fan1`, `fan2`, etc.

The app **never writes to PWM controls**.

## TrueNAS host prerequisite

On the Z590 Taichi, load the NCT6686D-compatible driver on the TrueNAS host:

```sh
modprobe nct6683 force=1
```

Verify:

```sh
dmesg | grep -iE 'nct6683|nct6686'
```

For persistence in TrueNAS, add `modprobe nct6683 force=1` as a **Post Init** command in System Settings -> Advanced -> Init/Shutdown Scripts.

## Run directly on TrueNAS

Clone the repository into a persistent dataset, for example `/mnt/pool1/truenas-hwmon`, then:

```sh
cd /mnt/pool1/truenas-hwmon
docker compose up -d --build
```

Open:

```text
http://TRUENAS-IP:30200
```

## GitHub / GHCR build

The included `.github/workflows/docker-publish.yml` runs the mock hardware test and publishes the image to GitHub Container Registry whenever you push to `main`/`master`, push a `v*` tag, or run the workflow manually. It uses the current Docker GitHub Actions major releases and publishes `latest` from the default branch.

For a repository named `scallbe1/truenas-hwmon`, the resulting image is:

```text
ghcr.io/scallbe1/truenas-hwmon:latest
```

If the GHCR package is private, either make the package public or configure registry credentials in TrueNAS.

## TrueNAS Custom App YAML

The repository includes `truenas-custom-app.yml`, already pointed at `ghcr.io/scallbe1/truenas-hwmon:latest`. Its contents are:

```yaml
services:
  truenas-hwmon:
    image: ghcr.io/YOUR-GITHUB-USER/truenas-hwmon:latest
    restart: unless-stopped
    ports:
      - "30200:8080"
    environment:
      HOST_SYS: /host/sys
      CONFIG_PATH: /config/config.json
      POLL_INTERVAL: "2"
      HISTORY_MINUTES: "60"
    volumes:
      - /sys:/host/sys:ro
      - /mnt/pool1/truenas-hwmon/config:/config:ro
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

Create `/mnt/pool1/truenas-hwmon/config/config.json` first using the included example.

## Fan labels

Edit `config/config.json` as you identify the motherboard headers:

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

No rebuild is needed. The config is re-read during polling.

## Security model

- `/sys` is mounted read-only.
- The container runs as UID 10001, not root.
- All Linux capabilities are dropped.
- `no-new-privileges` is enabled.
- The container filesystem is read-only except a tiny `/tmp` tmpfs.
- No Docker socket is mounted.
- The application has no API that writes fan/PWM values.

## API

- `GET /api/status` - latest sensor snapshot
- `GET /api/history` - in-memory history
- `GET /api/config` - effective configuration
- `GET /health` - health/discovery status

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `HOST_SYS` | `/host/sys` | Host sysfs mount |
| `CONFIG_PATH` | `/config/config.json` | Friendly labels/settings |
| `POLL_INTERVAL` | `2` | Polling interval in seconds |
| `HISTORY_MINUTES` | `60` | In-memory history retention |
