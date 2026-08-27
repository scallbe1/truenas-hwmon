from pathlib import Path
import shutil

root = Path('/tmp/mock-sys')
shutil.rmtree(root, ignore_errors=True)
base = root / 'class' / 'hwmon'


def put(device, name, files):
    d = base / device
    d.mkdir(parents=True, exist_ok=True)
    (d/'name').write_text(name+'\n')
    for k,v in files.items():
        fp=d/k
        fp.write_text(str(v)+'\n')
        if k.startswith('pwm'):
            fp.chmod(0o444)

put('hwmon0','coretemp',{
    'temp1_input':46000,'temp1_label':'Package id 0','temp2_input':42000,'temp2_label':'Core 0','temp3_input':43000,'temp3_label':'Core 1'
})
put('hwmon1','nct6686',{
    'temp1_input':46000,'temp1_label':'PECI 0.0','temp2_input':43000,'temp2_label':'Thermistor 14','temp3_input':39500,'temp3_label':'Thermistor 15',
    'fan1_input':2101,'pwm1':179,'fan2_input':0,'pwm2':0,'fan3_input':995,'pwm3':77,'fan4_input':805,'pwm4':45,'fan5_input':0,'pwm5':0,'fan6_input':4000,'pwm6':110
})
put('hwmon2','drivetemp',{'temp1_input':34000,'temp1_label':'Composite'})
print(root)
