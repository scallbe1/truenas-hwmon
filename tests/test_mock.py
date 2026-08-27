import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['HOST_SYS'] = '/tmp/mock-sys'
os.environ['HOST_PROC'] = '/tmp/mock-proc'
os.environ['CONFIG_PATH'] = str(ROOT / 'config' / 'config.json')

from app.main import read_all_sensors

s1 = read_all_sensors()
assert s1['cpu']['hottest_c'] == 46.0
assert s1['motherboard']['hottest_c'] == 46.0
assert len(s1['fans']) == 6
assert s1['fans'][0]['rpm'] == 2101
assert s1['fans'][0]['pwm_percent'] == 70
assert s1['fans'][5]['rpm'] == 4000
assert s1['memory']['total_bytes'] == 131072000 * 1024
assert s1['memory']['used_percent'] > 80
assert len(s1['disks']) == 1
assert s1['disks'][0]['name'] == 'sda'
assert s1['disks'][0]['temperature_c'] == 34.0
assert any(x['chip'] == 'drivetemp' for x in s1['other_temperatures'])
assert s1['gpu']['available'] is False  # CI/mock host has no NVIDIA runtime

# Advance CPU, disk and process counters so the rate sampler is exercised.
Path('/tmp/mock-proc/stat').write_text('cpu  1300 0 600 5200 100 0 0 0 0 0\n')
Path('/tmp/mock-proc/diskstats').write_text('8 0 sda 120 0 14000 0 80 0 26000 0 0 0 0\n')
p = Path('/tmp/mock-proc/4242')
rest = ['S','1','1','1','0','0','0','0','0','0','0','160','40','0','0','0','0','0','0','0']
(p / 'stat').write_text('4242 (mock-worker) ' + ' '.join(rest) + '\n')
(p / 'io').write_text('read_bytes: 3000000\nwrite_bytes: 5000000\n')
time.sleep(0.05)
s2 = read_all_sensors()
assert s2['cpu']['usage_percent'] is not None
assert s2['disks'][0]['read_mbps'] > 0
assert s2['disks'][0]['write_mbps'] > 0
assert s2['processes'][0]['pid'] == 4242
assert s2['processes'][0]['cpu_percent'] > 0
assert s2['processes'][0]['read_mbps'] is not None and s2['processes'][0]['read_mbps'] > 0
print('mock telemetry test: PASS')
