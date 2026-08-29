import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOCK_ROOT = Path(os.getenv('MOCK_ROOT', '/tmp'))
sys.path.insert(0, str(ROOT))
os.environ['HOST_SYS'] = str(MOCK_ROOT / 'mock-sys')
os.environ['HOST_PROC'] = str(MOCK_ROOT / 'mock-proc')
os.environ['DOCKER_CONTAINERS_ROOT'] = str(MOCK_ROOT / 'mock-docker')
os.environ['CONFIG_PATH'] = str(ROOT / 'config' / 'config.json')

from app import main as hwmon

hwmon.pynvml = None
read_all_sensors = hwmon.read_all_sensors

s1 = read_all_sensors()
assert s1['cpu']['hottest_c'] == 46.0
assert s1['motherboard']['hottest_c'] == 46.0
assert len(s1['fans']) == 6
assert s1['fans'][0]['rpm'] == 2101
assert s1['fans'][0]['pwm_percent'] == 70
assert s1['fans'][5]['rpm'] == 4000
assert len(s1['voltages']) == 8
assert s1['voltages'][1]['label'] == 'VIN0'
assert s1['voltages'][1]['volts'] == 0.768
assert s1['memory']['total_bytes'] == 131072000 * 1024
assert s1['memory']['used_percent'] > 80
assert len(s1['disks']) == 2
disks = {disk['name']: disk for disk in s1['disks']}
assert disks['sda']['temperature_c'] == 40.0
assert disks['sdb']['temperature_c'] == 35.0
drive_temps = [x for x in s1['other_temperatures'] if x['chip'] == 'drivetemp']
assert {x['block_device'] for x in drive_temps} == {'sda', 'sdb'}
assert len(s1['network_interfaces']) == 1
assert s1['network_interfaces'][0]['name'] == 'enp6s0'
assert s1['network_interfaces'][0]['speed_mbps'] == 2500
assert s1['network_interfaces'][0]['temperature_c'] == 31.0
assert s1['network_temperatures'][0]['chip'].startswith('r8169')
assert s1['gpu']['available'] is False

# Advance CPU, disk, process and network counters so rate sampling is exercised.
(MOCK_ROOT / 'mock-proc' / 'stat').write_text('cpu  1300 0 600 5200 100 0 0 0 0 0\n')
(MOCK_ROOT / 'mock-proc' / 'diskstats').write_text('8 0 sda 120 0 14000 0 80 0 26000 0 0 0 0\n')
p = MOCK_ROOT / 'mock-proc' / '4242'
rest = ['S','1','1','1','0','0','0','0','0','0','0','160','40','0','0','0','0','0','0','0']
(p / 'stat').write_text('4242 (mock-worker) ' + ' '.join(rest) + '\n')
(p / 'io').write_text('read_bytes: 3000000\nwrite_bytes: 5000000\n')
(p / 'net' / 'dev').write_text(
    'Inter-|   Receive                                                |  Transmit\n'
    ' face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n'
    '  eth0: 5000000 200 0 0 0 0 0 0 8000000 200 0 0 0 0 0 0\n'
)
net_stats = MOCK_ROOT / 'mock-sys' / 'class' / 'net' / 'enp6s0' / 'statistics'
(net_stats / 'rx_bytes').write_text('5000000\n')
(net_stats / 'tx_bytes').write_text('8000000\n')
time.sleep(0.05)
s2 = read_all_sensors()
assert s2['cpu']['usage_percent'] is not None
assert s2['disks'][0]['read_mbps'] > 0
assert s2['disks'][0]['write_mbps'] > 0
assert s2['top_cpu'][0]['pid'] == 4242
assert s2['top_cpu'][0]['cpu_percent'] > 0
assert s2['top_cpu'][0]['app_name'] == 'mock'
assert s2['top_cpu'][0]['service_name'] == 'worker'
assert s2['top_memory'][0]['pid'] == 4242
assert s2['top_memory'][0]['rss_bytes'] > 0
assert s2['top_disk'][0]['pid'] == 4242
assert s2['top_disk'][0]['disk_mbps'] > 0
assert s2['top_network'][0]['container_name'] == 'ix-mock-worker-1'
assert s2['top_network'][0]['total_mbps'] > 0
assert s2['top_network'][0]['processes'][0]['pid'] == 4242
assert s2['network_interfaces'][0]['rx_mbps'] > 0
assert s2['network_interfaces'][0]['tx_mbps'] > 0
print('mock telemetry v2.5 test: PASS')
