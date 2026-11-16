"""Execute the installer policy blocks with isolated files and firewall commands."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def block(start, end):
    source = (ROOT / 'install.sh').read_text(encoding='utf-8')
    return source[source.index(start):source.index(end, source.index(start))]


class InstallerUpdatePolicy(unittest.TestCase):
    def exercise(self, component, existing=True, legacy=(), managed=(), ports='2020-2030', proto='TCP'):
        source = (ROOT / 'install.sh').read_text(encoding='utf-8')
        helper = ''
        if 'xm_open_component_port() {' in source:
            helper = block('xm_open_component_port() {', '# Установка Mieru')
        config = block('    # Конфигурация Mieru', '    which_mita=')
        firewall = block('    ipt_proto=', '    echo -e "  -> Запуск и проверка службы mita')
        name, filename = 'mita', 'config.json'
        payload = '{"users":[],"portBindings":[]}\n'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            directory = base / 'etc' / name
            directory.mkdir(parents=True)
            if existing:
                (directory / filename).write_text(payload)
                (directory / 'users_db.json').write_text('{}\n')
            private = directory / 'tproxy-state.json'
            private.write_text('{"private":"synthetic"}\n')
            (base / 'legacy').write_text('\n'.join(legacy) + ('\n' if legacy else ''))
            (base / 'managed').write_text('\n'.join(managed) + ('\n' if managed else ''))
            backend = base / 'iptables'
            backend.write_text('''#!/usr/bin/env bash
set -eu
op=$1; shift
[ "$1" = INPUT ]; shift
[ "${1:-}" != 1 ] || shift
key="$*"
state="$FIXTURE/${BACKEND:-legacy}"
case "$op" in
    -C) grep -Fxq "$key" "$state";;
    -I) echo "$key" >> "$state";;
    *) echo "unsupported operation $op" >&2; exit 2;;
esac
''')
            backend.chmod(0o755)
            wrapper = base / 'run.sh'
            wrapper.write_text(f'''#!/usr/bin/env bash
set -eu
export FIXTURE="{base}"
export PATH="{base}:$PATH"
INSTALL_MIERU=yes
MIERU_PORTS="{ports}"
MIERU_PROTO="{proto}"
WAN_IF=eth0
XRAY_TPROXY_PORT=12345
XRAY_REDIRECT_PORT=12346
WORK_DIR="{base}"
xm_enable() {{ :; }}
xm_service() {{ :; }}
systemctl() {{ :; }}
{helper}
chmod() {{ printf 'chmod %s %s\\n' "$1" "$2"; }}
chown() {{ printf 'chown %s %s\\n' "$1" "$2"; }}
{config}
BACKEND=managed
{firewall}
''')
            wrapper.chmod(0o755)
            result = subprocess.run(['bash', str(wrapper)], capture_output=True, text=True, check=True)
            managed_rules = [line for line in (base / 'managed').read_text().splitlines() if line]
            return managed_rules, result.stdout.strip(), str(directory)

    def test_existing_installation_never_mutates_permissions_or_restores_dropped_rules(self):
        _, permissions, _ = self.exercise('mita')
        self.assertEqual(permissions, '')

    def test_new_mieru_range_and_single_port_open_selected_protocol(self):
        for ports, proto, expected in (('2020-2030', 'TCP', '2020:2030'), ('4040', 'UDP', '4040')):
            with self.subTest(ports=ports):
                rules, permissions, directory = self.exercise('mita', existing=False, ports=ports, proto=proto)
                self.assertEqual(rules, [f'-p {proto.lower()} --dport {expected} -j ACCEPT'])
                self.assertEqual(permissions.splitlines(), [
                    'chown mita:mita '+directory+'/config.json', 'chmod 640 '+directory+'/config.json',
                    'chown mita:mita '+directory+'/users_db.json', 'chmod 640 '+directory+'/users_db.json'])

    def test_ufw_migration_adopts_existing_mieru_rule(self):
        rule = '-p tcp --dport 2020:2030 -j ACCEPT'
        rules, _, _ = self.exercise('mita', legacy=(rule,))
        self.assertEqual(rules, [rule])

    def test_existing_ufw_policy_is_unchanged(self):
        rules = ('-p tcp --dport 2020:2030 -j ACCEPT',)
        actual, _, _ = self.exercise('mita', managed=rules)
        self.assertEqual(actual, list(rules))


if __name__ == '__main__':
    unittest.main()
