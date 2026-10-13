#!/usr/bin/env python3
"""Destructive acceptance ONLY in a disposable VM marked /.x-manager-test-lab.

Run after install.sh --direct, with XM_COMPONENT_DIR pointing to verified assets.
Creates synthetic local data; never use on a VPS containing user data.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import urllib.request

if not Path('/.x-manager-test-lab').is_file() or os.geteuid()!=0:
    sys.exit('Requires an explicitly marked disposable VM as root')
SOURCE=Path(__file__).resolve().parents[1]
PRIVATE=Path('/root/lite-acceptance');PRIVATE.mkdir(mode=0o700,exist_ok=True)
def run(name,args,ok=True,stdin=None):
    p=subprocess.run(args,input=stdin,text=True,capture_output=True,timeout=360)
    log=PRIVATE/(name+'.log');log.write_text(p.stdout+'\n'+p.stderr);log.chmod(0o600)
    if (p.returncode==0)!=ok:raise AssertionError(name+' unexpected exit '+str(p.returncode)+'; inspect '+str(log))
    print(name+': PASS',flush=True);return p
def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
def files():
    names=['/etc/snell/snell-server.conf','/etc/mita/config.json','/etc/mita/users_db.json','/etc/tuna-subscriptions/config.toml','/etc/x-manager/gateways.env']
    return {n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in names}
config=tomllib.loads(Path('/etc/tuna-subscriptions/config.toml').read_text())
BASE='http://127.0.0.1:'+str(config['server']['port'])
def api(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
def user_row():
    with sqlite3.connect(config['database']['path']) as db:
        return db.execute('SELECT * FROM users WHERE nickname=?',('lite-qa',)).fetchone()
for unit in ('snell','mita','tuna-subscriptions'):assert active(unit),unit
assert config['server']['bind_address']=='127.0.0.1'
assert not Path('/usr/local/bin/openflux').exists()
assert not Path('/etc/webdav-tunnel').exists()
api('/api/users',{'nickname':'lite-qa','custom_uri':'vless://11111111-1111-4111-8111-111111111111@example.invalid:10001?security=none#QA'})
row=user_row();before=files()
run('manual-stop',['python3',str(SOURCE/'scripts/service-control.py'),'stop','mita'])
run('repeat-install',['bash',str(SOURCE/'install.sh'),'--direct'])
assert files()==before and user_row()==row
assert not active('mita') and active('snell') and active('tuna-subscriptions')
run('resume-mita',['python3',str(SOURCE/'scripts/service-control.py'),'start','mita'])
run('missing-asset',['env','XM_COMPONENT_DIR=/opt/xm-lite/missing','bash',str(SOURCE/'install.sh'),'--direct'],ok=False)
assert files()==before and user_row()==row and active('mita')
with tempfile.TemporaryDirectory() as t:
    candidate=Path(t)/'candidate';shutil.copytree(SOURCE,candidate)
    installer=candidate/'install.sh';text=installer.read_text()
    assert 'ExecStart=${which_mita} run' in text
    installer.write_text(text.replace('ExecStart=${which_mita} run','ExecStart=/bin/false'))
    run('start-failure-rollback',['bash',str(installer),'--direct'],ok=False)
assert files()==before and user_row()==row and active('mita') and active('snell') and active('tuna-subscriptions')
run('tcp-preview',['python3',str(SOURCE/'scripts/tcp-profile.py'),'plan','--notsent-lowat','--keep-cwnd','--fastopen'])
keys=('net.ipv4.tcp_notsent_lowat','net.ipv4.tcp_slow_start_after_idle','net.ipv4.tcp_fastopen')
old={k:Path('/proc/sys/'+k.replace('.','/')).read_text() for k in keys}
run('tcp-apply',['python3',str(SOURCE/'scripts/tcp-profile.py'),'apply','--notsent-lowat','--keep-cwnd','--fastopen'])
run('tcp-rollback',['python3',str(SOURCE/'scripts/tcp-profile.py'),'rollback'])
assert old=={k:Path('/proc/sys/'+k.replace('.','/')).read_text() for k in keys}
run('masterdns-install',['bash',str(SOURCE/'scripts/install-masterdns.sh'),'tunnel.example.invalid'])
assert active('masterdns')
assert tomllib.loads(Path('/etc/masterdns/server_config.toml').read_text())['USE_EXTERNAL_SOCKS5'] is False
run('snell6-create',['python3',str(SOURCE/'scripts/snell6-endpoints.py'),'create'],stdin=json.dumps({'name':'Lite QA Snell6','server_host':'127.0.0.1','routing':'direct','mode':'default','probe_url':'https://example.com/'}))
run('snell6-switch',['python3',str(SOURCE/'scripts/snell-switch.py'),'switch','--version','6','--slot','1'])
assert active('snell6@1') and not active('snell')
uid=api('/api/users/lite-qa')['id']
run('snell6-publish',['python3',str(SOURCE/'scripts/snell6-endpoints.py'),'publish','--user',uid])
user=api('/api/users/lite-qa')
assert len([u for u in user['snell_uris'] if 'version=6' in u])==1
run('repeat-publish',['python3',str(SOURCE/'scripts/snell6-endpoints.py'),'publish','--user',uid])
assert len(api('/api/users/lite-qa')['snell_uris'])==1
before=files();row=user_row()
run('update-with-v6',['bash',str(SOURCE/'install.sh'),'--update'])
assert files()==before and user_row()==row and active('snell6@1') and not active('snell')
run('switch-v5',['python3',str(SOURCE/'scripts/snell-switch.py'),'switch','--version','5'])
assert active('snell') and not active('snell6@1')
run('menu-exit',['bash','/usr/local/bin/x-manager'],stdin='0\n')
print('ACCEPTANCE PASSED: isolated VM only; remote client/DNS delegation not tested',flush=True)
