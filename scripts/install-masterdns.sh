#!/usr/bin/env bash
# Explicit setup; never assume a domain owned by somebody else.
set -eEuo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. "$SCRIPT_DIR/scripts/installer-common.sh"
xm_preflight
python3 "$SCRIPT_DIR/scripts/lite-edition.py" check
[ "$#" = 1 ] || xm_die 'Укажите домен DNS-зоны'
python3 - "$1" <<'PY'
import re,sys
domain=sys.argv[1]
if len(domain)>253 or '.' not in domain or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',x) for x in domain.split('.')):
    sys.exit('Некорректный домен DNS-зоны')
PY
for dep in curl python3 openssl ss iptables; do command -v "$dep" >/dev/null || xm_die "Нет зависимости: $dep"; done
listen=0.0.0.0
if [ ! -f /etc/masterdns/server_config.toml ]; then
    listen=$(python3 - <<'PY'
import json,socket,subprocess,sys
def free(host):
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.bind((host,53))
        return True
    except OSError:return False
if free('0.0.0.0'):print('0.0.0.0')
else:
    route=json.loads(subprocess.check_output(['ip','-j','route','get','1.1.1.1']))
    host=route[0].get('prefsrc','') if route else ''
    if not host or not free(host):sys.exit('UDP-порт 53 занят на адресе сервера. Существующая DNS-служба не изменена.')
    print(host)
PY
    )
    echo "MasterDNS будет слушать $listen:53/UDP; системный DNS сохраняется."
fi
WORK_DIR=$(mktemp -d)
python3 "$SCRIPT_DIR/scripts/release-assets.py" 'masterdns-{arch}.tar.gz' "$WORK_DIR/masterdns.tar.gz"
python3 "$SCRIPT_DIR/scripts/extract-masterdns.py" "$WORK_DIR/masterdns.tar.gz" "$WORK_DIR/masterdns-server"
xm_begin
install -d -m 0700 /etc/masterdns
install -m 0755 "$WORK_DIR/masterdns-server" /usr/local/bin/masterdns-server
if [ ! -f /etc/masterdns/encrypt_key.txt ]; then
    ( umask 077; openssl rand -hex 16 > /etc/masterdns/encrypt_key.txt )
fi
[ ! -r /etc/x-manager/gateways.env ] || . /etc/x-manager/gateways.env
route=xray
[ ! -f /etc/snell/routing.mode ] || route=$(cat /etc/snell/routing.mode)
python3 - "$1" "${XRAY_SOCKS_PORT:-10808}" "$route" "$listen" <<'PY'
from pathlib import Path
import json,re,sys
path=Path('/etc/masterdns/server_config.toml')
if path.is_symlink():sys.exit('Конфигурация MasterDNS не должна быть symlink')
domain=json.dumps(sys.argv[1]);port=int(sys.argv[2])
if not 1<=port<=65535 or port in (443,8443):sys.exit('Некорректный порт шлюза')
if path.exists():
    text=path.read_text()
    if len(re.findall(r'^DOMAIN\s*=',text,re.M))!=1:sys.exit('Некорректная конфигурация DOMAIN')
    text=re.sub(r'^DOMAIN\s*=.*$',lambda _: 'DOMAIN = ['+domain+']',text,flags=re.M)
else:
    text=f'''DOMAIN = [{domain}]
PROTOCOL_TYPE = "SOCKS5"
UDP_HOST = {json.dumps(sys.argv[4])}
UDP_PORT = 53
UDP_READERS = 4
DNS_REQUEST_WORKERS = 4
MAX_CONCURRENT_REQUESTS = 16384
SOCKET_BUFFER_SIZE = 8388608
MAX_PACKET_SIZE = 4096
DNS_UPSTREAM_SERVERS = ["1.1.1.1:53", "8.8.8.8:53"]
USE_EXTERNAL_SOCKS5 = {str(sys.argv[3] == "xray").lower()}
SOCKS5_AUTH = false
FORWARD_IP = "127.0.0.1"
FORWARD_PORT = {port}
DATA_ENCRYPTION_METHOD = 5
ENCRYPTION_KEY_FILE = "/etc/masterdns/encrypt_key.txt"
LOG_LEVEL = "INFO"
'''
import tomllib
tomllib.loads(text)
temp=path.with_suffix('.new');temp.write_text(text);temp.chmod(0o600);temp.replace(path)
PY
xm_write_unit /etc/systemd/system/masterdns.service <<'EOF'
[Unit]
Description=MasterDnsVPN DNS Tunnel
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=/etc/masterdns
ExecStart=/usr/local/bin/masterdns-server -config /etc/masterdns/server_config.toml
Restart=on-failure
RestartSec=5
LimitNOFILE=65535
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
python3 "$SCRIPT_DIR/scripts/service-control.py" reconcile
xm_enable masterdns
xm_service masterdns
python3 "$SCRIPT_DIR/scripts/tuna-watchdog.py" defaults --attempts 5 --delay 5
if [ ! -f /etc/x-manager/dns_wan_blocked.lock ]; then
    iptables -C INPUT -p udp --dport 53 -j ACCEPT 2>/dev/null || iptables -I INPUT -p udp --dport 53 -j ACCEPT
fi
xm_finish
echo 'MasterDNS настроен. Проверьте делегирование DNS-зоны и подключение клиента.'
