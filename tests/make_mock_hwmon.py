from pathlib import Path
import json
import os
import shutil
import subprocess

mock_root = Path(os.getenv('MOCK_ROOT', '/tmp'))
sysroot = mock_root / 'mock-sys'
procroot = mock_root / 'mock-proc'
dockerroot = mock_root / 'mock-docker'
shutil.rmtree(sysroot, ignore_errors=True)
shutil.rmtree(procroot, ignore_errors=True)
shutil.rmtree(dockerroot, ignore_errors=True)
base = sysroot / 'class' / 'hwmon'
base.mkdir(parents=True, exist_ok=True)
procroot.mkdir(parents=True, exist_ok=True)
dockerroot.mkdir(parents=True, exist_ok=True)


def link_directory(target: Path, link: Path):
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            raise
        subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(link), str(target.resolve())],
            check=True,
            capture_output=True,
        )


def put_dir(path: Path, name: str, files: dict[str, object]):
    path.mkdir(parents=True, exist_ok=True)
    (path / 'name').write_text(name + '\n')
    for k, v in files.items():
        fp = path / k
        fp.write_text(str(v) + '\n')
        if k.startswith('pwm') and os.name != 'nt':
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
    'in0_input': 0, 'in0_label': 'VIN16',
    'in1_input': 768, 'in1_label': 'VIN0',
    'in2_input': 992, 'in2_label': 'VIN1',
    'in3_input': 992, 'in3_label': 'VIN2',
    'in4_input': 1680, 'in4_label': 'VIN3',
    'in5_input': 1040, 'in5_label': 'VIN5',
    'in6_input': 1120, 'in6_label': 'VIN6',
    'in7_input': 1344, 'in7_label': 'VIN7',
})

# Real drivetemp nodes are children of a SCSI device while block/sdX is a
# sibling. Make two out-of-order devices so tests cannot rely on enumeration.
scsi1 = sysroot / 'devices' / 'mock' / 'host0' / 'target0_0_1' / '0_0_1_0'
scsi2 = sysroot / 'devices' / 'mock' / 'host0' / 'target0_0_2' / '0_0_2_0'
drive_hwmon1 = scsi1 / 'hwmon' / 'hwmon2'
drive_hwmon2 = scsi2 / 'hwmon' / 'hwmon4'
put_dir(drive_hwmon1, 'drivetemp', {'temp1_input': 35000, 'temp1_label': 'Composite'})
put_dir(drive_hwmon2, 'drivetemp', {'temp1_input': 40000, 'temp1_label': 'Composite'})
link_directory(drive_hwmon1, base / 'hwmon2')
link_directory(drive_hwmon2, base / 'hwmon4')

# Make a NIC hwmon sensor and physical interface share the same PCI owner.
# Windows cannot create colon-containing fixture paths, so use an equivalent mock spelling.
pci_root_name = 'pci0000_00' if os.name == 'nt' else 'pci0000:00'
pci_device_name = '0000_06_00.0' if os.name == 'nt' else '0000:06:00.0'
nic_pci = sysroot / 'devices' / pci_root_name / pci_device_name
nic_hwmon = nic_pci / 'hwmon' / 'hwmon3'
put_dir(nic_hwmon, 'r8169_0_600:00', {'temp1_input': 31000})
link_directory(nic_hwmon, base / 'hwmon3')

interface = sysroot / 'class' / 'net' / 'enp6s0'
(interface / 'statistics').mkdir(parents=True)
link_directory(nic_pci, interface / 'device')
(interface / 'address').write_text('00:11:22:33:44:55\n')
(interface / 'operstate').write_text('up\n')
(interface / 'carrier').write_text('1\n')
(interface / 'speed').write_text('2500\n')
(interface / 'duplex').write_text('full\n')
(interface / 'mtu').write_text('1500\n')
for key, value in {
    'rx_bytes': 1000000, 'tx_bytes': 2000000,
    'rx_packets': 1000, 'tx_packets': 2000,
    'rx_errors': 0, 'tx_errors': 0,
    'rx_dropped': 0, 'tx_dropped': 0,
}.items():
    (interface / 'statistics' / key).write_text(f'{value}\n')

for name, scsi, model in (
    ('sda', scsi2, 'MockDisk A 8TB'),
    ('sdb', scsi1, 'MockDisk B 8TB'),
):
    inventory_device = sysroot / 'block' / name / 'device'
    inventory_device.mkdir(parents=True, exist_ok=True)
    (inventory_device / 'vendor').write_text('ATA\n')
    (inventory_device / 'model').write_text(model + '\n')
    class_block = sysroot / 'class' / 'block' / name
    class_block.mkdir(parents=True, exist_ok=True)
    link_directory(scsi, class_block / 'device')

(procroot / 'meminfo').write_text(
    'MemTotal:       131072000 kB\n'
    'MemFree:         8000000 kB\n'
    'MemAvailable:   24000000 kB\n'
    'Buffers:          512000 kB\n'
    'Cached:          8000000 kB\n'
)
(procroot / 'loadavg').write_text('1.25 1.10 0.95 2/500 12345\n')
(procroot / 'stat').write_text('cpu  1000 0 500 5000 100 0 0 0 0 0\n')
(procroot / 'diskstats').write_text(
    '8 0 sda 100 0 10000 0 50 0 20000 0 0 0 0\n'
    '8 16 sdb 90 0 9000 0 40 0 18000 0 0 0 0\n'
)

cid = 'a' * 64
cdir = dockerroot / cid
cdir.mkdir()
(cdir / 'config.v2.json').write_text(json.dumps({
    'Name': '/ix-mock-worker-1',
    'Config': {
        'Image': 'example/mock:latest',
        'Labels': {
            'com.docker.compose.project': 'ix-mock',
            'com.docker.compose.service': 'worker',
        },
    },
}))

p = procroot / '4242'
p.mkdir()
# Parser needs fields through stime; state is field 3, utime/stime are fields 14/15.
rest = ['S','1','1','1','0','0','0','0','0','0','0','100','20','0','0','0','0','0','0','0']
(p / 'stat').write_text('4242 (mock-worker) ' + ' '.join(rest) + '\n')
(p / 'statm').write_text('100000 25000 0 0 0 0 0\n')
(p / 'cmdline').write_bytes(b'python\x00mock_worker.py\x00')
(p / 'io').write_text('read_bytes: 1000000\nwrite_bytes: 2000000\n')
(p / 'cgroup').write_text(f'0::/docker/{cid}\n')
(p / 'ns').mkdir()
if os.name == 'nt':
    (p / 'ns' / 'net').write_text('net:[4026532999]\n')
else:
    os.symlink('net:[4026532999]', p / 'ns' / 'net')
(p / 'net').mkdir()
(p / 'net' / 'dev').write_text(
    'Inter-|   Receive                                                |  Transmit\n'
    ' face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n'
    '  eth0: 1000000 100 0 0 0 0 0 0 2000000 100 0 0 0 0 0 0\n'
    '    lo: 500000 10 0 0 0 0 0 0 500000 10 0 0 0 0 0 0\n'
)

print(sysroot)
print(procroot)
print(dockerroot)
