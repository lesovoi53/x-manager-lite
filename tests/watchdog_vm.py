#!/usr/bin/env python3
"""Real systemd recovery test in a marked, disposable VM; never a real CSQTT."""
import json
from pathlib import Path
import subprocess
import time

assert Path('/.x-manager-test-lab').is_file(), 'Disposable VM required'
unit=Path('/etc/systemd/system/csqtt.service')
policy=Path('/etc/systemd/system/csqtt.service.d/90-tuna-watchdog.conf')
assert not unit.exists() and not policy.exists(), 'Test requires no existing CSQTT'
work=Path('/opt/xm-lite/watchdog-fixture');work.mkdir(mode=0o700,exist_ok=True)
flag=work/'healthy';flag.unlink(missing_ok=True)
script=work/'run.sh';script.write_text('#!/bin/sh\n[ -f '+str(flag)+' ] || exit 1\nexec /bin/sleep infinity\n');script.chmod(0o700)
unit.write_text('[Unit]\nDescription=Disposable Lite watchdog fixture\n[Service]\nExecStart='+str(script)+'\n')
def call(*args):return subprocess.run(args,text=True,capture_output=True,check=True).stdout
def state():return dict(line.split('=',1) for line in call('systemctl','show','csqtt','-p','ActiveState','-p','NRestarts','-p','StartLimitBurst','-p','RestartUSec').splitlines())
try:
 call('systemctl','daemon-reload')
 call('python3','/usr/local/share/x-manager/scripts/tuna-watchdog.py','defaults')
 assert state()['StartLimitBurst']=='6' and state()['RestartUSec']=='5s'
 call('systemctl','start','csqtt')
 end=time.monotonic()+75
 while state()['ActiveState']!='failed' and time.monotonic()<end:time.sleep(2)
 assert state()['ActiveState']=='failed',state()
 assert state()['NRestarts']=='6',state()
 print('Crash limit: initial start + 5 retries; further start blocked',flush=True)
 call('python3','/usr/local/share/x-manager/scripts/tuna-recovery.py')
 since=json.loads(Path('/var/lib/x-manager/recovery/state.json').read_text())['csqtt']
 flag.touch()
 end=time.monotonic()+370
 while state()['ActiveState']!='active' and time.monotonic()<end:time.sleep(5)
 assert state()['ActiveState']=='active',state()
 elapsed=time.time()-since;assert elapsed>=300,elapsed
 print('Automatic reset and recovery after cooldown: '+str(round(elapsed))+' seconds',flush=True)
 call('python3','/usr/local/share/x-manager/scripts/service-control.py','stop','csqtt')
 call('python3','/usr/local/share/x-manager/scripts/tuna-recovery.py')
 time.sleep(10);assert state()['ActiveState']=='inactive'
 print('Manual stop respected: PASS',flush=True)
finally:
 subprocess.run(['systemctl','stop','csqtt'],check=True)
 unit.unlink();policy.unlink(missing_ok=True)
 subprocess.run(['systemctl','daemon-reload'],check=True)
