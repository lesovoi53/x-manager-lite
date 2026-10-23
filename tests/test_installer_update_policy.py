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
            helper = block('xm_open_component_port() {', '# Установка Snell')
        if component == 'snell':
            config = block('    # Конфигурация Snell', '    # TCP REDIRECT')
            firewall = block('    # Открытие TCP и UDP', '    systemctl daemon-reload\n    xm_enable snell')
            name, filename = 'snell', 'snell-server.conf'
            payload = '[snell-server]\nlisten = 0.0.0.0:1488\npsk = synthetic\n'
        else:
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
-C) grep -Fxq -- "$key" "$state" ;;
-I) printf '%s\\n' "$key" >> "$state" ;;
*) exit 98 ;;
esac
''', encoding='utf-8', newline='\n')
            backend.chmod(0o755)
            script = '''set -e
if command -v cygpath >/dev/null; then FIXTURE=$(cygpath -u "$FIXTURE"); export FIXTURE; fi
export PATH="$FIXTURE:$PATH"
iptables() { BACKEND=managed command iptables "$@"; }
chown() { printf 'chown %s\\n' "$*" >> "$FIXTURE/permissions"; }
chmod() { printf 'chmod %s\\n' "$*" >> "$FIXTURE/permissions"; }
jq() { printf '{}\\n'; }
xm_die() { echo "$*" >&2; exit 1; }
SNELL_PORT=1488
SNELL_PSK=synthetic
SNELL_OBFS=off
DEFAULT_ROUTING=direct
MIERU_USER=synthetic
MIERU_PASS=synthetic
MIERU_ENTROPY_MODE=LOW_ENTROPY_MODE_48
MIERU_MASK_ROTATION=LOW_ENTROPY_MASK_ROTATE_RIGHT_7
XRAY_SOCKS_PORT=10808
SERVER_IP=192.0.2.1
'''
            script += helper + '\n' + config + '\n' + firewall
            script = script.replace('/etc/' + name, directory.as_posix())
            env = dict(os.environ, FIXTURE=base.as_posix(), MIERU_PORTS=ports, MIERU_PROTO=proto)
            result = subprocess.run([os.environ.get('XM_TEST_BASH', 'bash'), '-c', script], env=env,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            permissions = (base / 'permissions').read_text() if (base / 'permissions').exists() else ''
            if existing:
                self.assertEqual((directory / filename).read_text(), payload)
            self.assertEqual(private.read_text(), '{"private":"synthetic"}\n')
            return (base / 'managed').read_text().splitlines(), permissions, directory.as_posix()

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory permissions')
    def test_new_directories_are_traversable_and_existing_modes_are_preserved(self):
        for name in ('snell', 'mita'):
            for existing in (False, True):
                with self.subTest(component=name, existing=existing), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp) / 'etc' / name
                    if existing:
                        directory.mkdir(parents=True, mode=0o700)
                        directory.chmod(0o700)
                    snippet = block(f'    [ -d /etc/{name} ]', '    mkdir -p /usr/local/bin')
                    snippet = snippet.replace('/etc/' + name, directory.as_posix())
                    result = subprocess.run(['bash', '-c', 'set -e; umask 077\n' + snippet],
                                            text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700 if existing else 0o755)
                    self.assertEqual(directory.stat().st_uid, os.geteuid())

    def test_update_keeps_missing_accept_and_existing_drop(self):
        for component in ('snell', 'mita'):
            with self.subTest(component=component):
                drops = ('-p tcp --dport 1488 -j DROP', '-p tcp --dport 2020:2030 -j DROP')
                rules, _, _ = self.exercise(component, managed=drops)
                self.assertEqual(rules, list(drops))

    def test_update_preserves_existing_config_and_private_state_permissions(self):
        for component in ('snell', 'mita'):
            with self.subTest(component=component):
                _, permissions, _ = self.exercise(component)
                self.assertEqual(permissions, '')

    def test_new_snell_opens_both_protocols_and_owns_only_new_config(self):
        rules, permissions, directory = self.exercise('snell', existing=False)
        self.assertEqual(rules, ['-p tcp --dport 1488 -j ACCEPT', '-p udp --dport 1488 -j ACCEPT'])
        self.assertEqual(permissions.splitlines(), ['chown snell:snell '+directory+'/snell-server.conf',
                                                  'chmod 640 '+directory+'/snell-server.conf'])

    def test_new_mieru_range_and_single_port_open_selected_protocol(self):
        for ports, proto, expected in (('2020-2030', 'TCP', '2020:2030'), ('4040', 'UDP', '4040')):
            with self.subTest(ports=ports):
                rules, permissions, directory = self.exercise('mita', existing=False, ports=ports, proto=proto)
                self.assertEqual(rules, [f'-p {proto.lower()} --dport {expected} -j ACCEPT'])
                self.assertEqual(permissions.splitlines(), [
                    'chown mita:mita '+directory+'/config.json', 'chmod 640 '+directory+'/config.json',
                    'chown mita:mita '+directory+'/users_db.json', 'chmod 640 '+directory+'/users_db.json'])

    def test_ufw_migration_adopts_only_existing_snell_protocol(self):
        rule = '-p udp --dport 1488 -j ACCEPT'
        rules, _, _ = self.exercise('snell', legacy=(rule,))
        self.assertEqual(rules, [rule])

    def test_ufw_migration_adopts_existing_mieru_rule(self):
        rule = '-p tcp --dport 2020:2030 -j ACCEPT'
        rules, _, _ = self.exercise('mita', legacy=(rule,))
        self.assertEqual(rules, [rule])

    def test_existing_ufw_policy_is_unchanged(self):
        for component, rules in (('snell', ('-p tcp --dport 1488 -j ACCEPT',)),
                                 ('mita', ('-p tcp --dport 2020:2030 -j ACCEPT',))):
            with self.subTest(component=component):
                actual, _, _ = self.exercise(component, managed=rules)
                self.assertEqual(actual, list(rules))


if __name__ == '__main__':
    unittest.main()
