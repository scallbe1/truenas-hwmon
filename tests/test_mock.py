import os
from pathlib import Path

os.environ['HOST_SYS']='/tmp/mock-sys'
os.environ['CONFIG_PATH']=str(Path(__file__).resolve().parents[1] / 'config' / 'config.json')

from app.main import read_all_sensors

s = read_all_sensors()
assert s['cpu']['hottest_c'] == 46.0
assert s['motherboard']['hottest_c'] == 46.0
assert len(s['fans']) == 6
assert s['fans'][0]['rpm'] == 2101
assert s['fans'][0]['pwm_percent'] == 70
assert s['fans'][5]['rpm'] == 4000
assert any(x['chip'] == 'drivetemp' for x in s['other_temperatures'])
print('mock sensor test: PASS')
