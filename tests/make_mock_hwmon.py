from pathlib import Path
import os
import shutil

sysroot = Path('/tmp/mock-sys')
procroot = Path('/tmp/mock-proc')
shutil.rmtree(sysroot, ignore_errors=True)
shutil.rmtree(procroot, ignore_errors=True)
base = sysroot / 'class' / 'hwmon'
base.mkdir(parents=True, exist_ok=True)
procroot.mkdir(parents=True, exist_ok=True)


def put_dir(path: Path, name: str, files: dict[str, object]):
    path.mkdir(parents=True, exist_ok=True)
    (path / 'name').write_text(name + '\n')
    for k, v in files.items():
        fp = path / k
        fp.write_text(str(v) + '\n')
        if k.startswith('pwm'):
            fp.chmod(0o444)


def put_hwmon(device: str, name: str, files: dict[str, object]):
    put_dir(base / device, name, files)


put_hwmon('hwmon0', 'coretemp', {
    'temp1_input': 46000, 'temp1_label': 'Package id 0',
    'temp2_input': 42000, 'temp2_label': 'Core 0',
    'temp3_input': 43000, 'temp3_label': 'Core 1',
})
put_hwmon('hwmon1', 'nct6686', {
    'temp1_input': 46000, 'temp1_label': 'PECI 0.0',
    'temp2_input': 43000, 'temp2_label': 'Thermistor 14',
    'temp3_input': 39500, 'temp3_label': 'Thermistor 15',
    'fan1_input': 2101, 'pwm1': 179,
    'fan2_input': 0, 'pwm2': 0,
    'fan3_input': 995, 'pwm3': 77,
    'fan4_input': 805, 'pwm4': 45,
    'fan5_input': 0, 'pwm5': 0,
    'fan6_input': 4000, 'pwm6': 110,
})

# Make drivetemp resemble real sysfs so the monitor can map temp -> sda.
drive_hwmon = sysroot / 'devices' / 'mock' / 'block' / 'sda' / 'device' / 'hwmon' / 'hwmon2'
put_dir(drive_hwmon, 'drivetemp', {'temp1_input': 34000, 'temp1_label': 'Composite'})
os.symlink('../../devices/mock/block/sda/device/hwmon/hwmon2', base / 'hwmon2')

(sysroot / 'block' / 'sda' / 'device').mkdir(parents=True, exist_ok=True)
(sysroot / 'block' / 'sda' / 'device' / 'vendor').write_text('ATA\n')
(sysroot / 'block' / 'sda' / 'device' / 'model').write_text('MockDisk 8TB\n')

(procroot / 'meminfo').write_text(
    'MemTotal:       131072000 kB\n'
    'MemFree:         8000000 kB\n'
    'MemAvailable:   24000000 kB\n'
    'Buffers:          512000 kB\n'
    'Cached:          8000000 kB\n'
)
(procroot / 'loadavg').write_text('1.25 1.10 0.95 2/500 12345\n')
(procroot / 'stat').write_text('cpu  1000 0 500 5000 100 0 0 0 0 0\n')
(procroot / 'diskstats').write_text('8 0 sda 100 0 10000 0 50 0 20000 0 0 0 0\n')

p = procroot / '4242'
p.mkdir()
# Parser needs fields through stime; state is field 3, utime/stime are fields 14/15.
rest = ['S','1','1','1','0','0','0','0','0','0','0','100','20','0','0','0','0','0','0','0']
(p / 'stat').write_text('4242 (mock-worker) ' + ' '.join(rest) + '\n')
(p / 'statm').write_text('100000 25000 0 0 0 0 0\n')
(p / 'cmdline').write_bytes(b'python\x00mock_worker.py\x00')
(p / 'io').write_text('read_bytes: 1000000\nwrite_bytes: 2000000\n')

print(sysroot)
print(procroot)
