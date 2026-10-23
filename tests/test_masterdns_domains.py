"""Multidomain editing, lifecycle rollback and the real shell export function."""
import base64
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('masterdns_domains_test', ROOT / 'scripts/masterdns-domains.py')
domains = importlib.util.module_from_spec(spec)
spec.loader.exec_module(domains)
ORIGINAL = b'''# keep this comment\r\nDOMAIN = [\r\n  "d.example.org", # first\r\n  'd1.example.org',\r\n] # zone list\r\nUSE_EXTERNAL_SOCKS5 = true\r\nFORWARD_PORT = 10808\r\nENCRYPTION_KEY_FILE = "/private/key"\r\n'''


def decoded_profile(link):
    if link.startswith('tuna://'):
        encoded = link.removeprefix('tuna://')
        document = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))))
        if set(document) != {'name', 'uri'}:
            raise AssertionError('Expected exactly one quick-input profile, not a group or subscription')
        link = document['uri']
    return json.loads(base64.b64decode(link.removeprefix('stormdns://')))


class Control:
    def __init__(self, active=True, allowed=True, fail=0):
        self.active = active
        self.allowed_value = allowed
        self.fail = fail
        self.calls = []
        self.locked = False
        self.locks = 0

    @contextmanager
    def lock(self):
        if self.locked:
            raise AssertionError('nested lifecycle lock')
        self.locked = True
        self.locks += 1
        try:
            yield
        finally:
            self.locked = False

    def show(self, unit):
        return dict(LoadState='loaded', ActiveState='active' if self.active else 'inactive', UnitFileState='disabled')

    def allowed(self, unit):
        return self.allowed_value

    def check_owned(self, unit):
        pass

    def ctl(self, action, unit):
        assert self.locked
        self.calls.append((action, unit))
        if self.fail and action == 'restart':
            self.fail -= 1
            self.active = False
            raise RuntimeError('mock restart failure')
        self.active = action != 'stop'


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'server.toml'
        self.config.write_bytes(ORIGINAL)
        self.config.chmod(0o640)

    def save(self, values, control):
        return domains.save(self.config, values, ORIGINAL, control, sleep=lambda seconds: None)

    def test_limits_case_insensitive_duplicates_and_dns_syntax(self):
        for values in ([], ['d.example.org'] * 2, ['d.example.org', 'D.EXAMPLE.ORG'],
                       ['d..org'], ['d.example.org.'], ['*.example.org'], ['x'], ['x y.org'],
                       ['-x.example.org'], ['x' * 64 + '.org'], ['d%d.example.org' % i for i in range(17)]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                domains.validate(values)
        self.assertEqual(len(domains.validate(['d%d.example.org' % i for i in range(16)])), 16)

    def test_one_two_four_and_non_domain_bytes_preserved(self):
        for count in (1, 2, 4):
            values = ['d%d.example.org' % i for i in range(count)]
            result = domains.replace_domains(ORIGINAL.decode(), values)
            self.assertEqual(domains.read_domains(result), values)
            self.assertEqual(result.split('] # zone list', 1)[1], ORIGINAL.decode().split('] # zone list', 1)[1])
            self.assertTrue(result.startswith('# keep this comment\r\nDOMAIN = ['))

    def test_shadow_assignment_inside_string_rejected_without_other_changes(self):
        text = "NOTE = '''\nDOMAIN = ['fake.example.org']\n'''\nDOMAIN = ['real.example.org']\n"
        with self.assertRaises(ValueError):
            domains.replace_domains(text, ['new.example.org'])

    def test_active_save_uses_one_lock_and_preserves_metadata(self):
        before = self.config.stat()
        control = Control()
        self.assertTrue(self.save(['plain.example.org', 'd.example.org'], control))
        self.assertEqual(control.calls, [('restart', domains.UNIT)])
        self.assertEqual(control.locks, 1)
        self.assertEqual(self.config.stat().st_mode, before.st_mode)
        if os.name == 'posix':
            self.assertEqual((self.config.stat().st_uid, self.config.stat().st_gid), (before.st_uid, before.st_gid))

    def test_unchanged_list_does_not_reformat_or_restart(self):
        control = Control()
        self.assertFalse(self.save(['d.example.org', 'd1.example.org'], control))
        self.assertEqual(self.config.read_bytes(), ORIGINAL)
        self.assertFalse(control.calls)

    def test_stopped_and_inhibited_stay_stopped(self):
        for allowed in (True, False):
            self.config.write_bytes(ORIGINAL)
            control = Control(active=False, allowed=allowed)
            self.assertFalse(self.save(['plain.example.org'], control))
            self.assertFalse(control.calls)

    def test_active_inhibited_refuses_before_write(self):
        control = Control(allowed=False)
        with self.assertRaises(ValueError):
            self.save(['new.example.org'], control)
        self.assertEqual(self.config.read_bytes(), ORIGINAL)
        self.assertFalse(control.calls)

    def test_real_lifecycle_controller_preserves_off_stop_and_autostart_intent(self):
        fixture_spec = importlib.util.spec_from_file_location('masterdns_service_fixture', ROOT / 'tests/test_service_control.py')
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        for action in ('off', 'stop', 'autostart-off'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                systemd = fixture.Systemd(root)
                control = fixture.control.Controller(root, systemd)
                control.change(action, domains.UNIT)
                before = control.state_path.read_bytes()
                markers = [(p, p.read_bytes() if p.exists() else None) for p in control.markers(domains.UNIT)]
                self.config.write_bytes(ORIGINAL)
                systemd.calls.clear()
                self.save(['plain.example.org'], control)
                self.assertEqual(control.state_path.read_bytes(), before)
                for path, content in markers:
                    self.assertEqual(path.read_bytes() if path.exists() else None, content)
                mutations = [call[1] for call in systemd.calls if call[1] != 'show']
                self.assertEqual(mutations, ['restart'] if action == 'autostart-off' else [])

    def test_restart_failure_rolls_back_config_and_service(self):
        control = Control(fail=1)
        with self.assertRaisesRegex(RuntimeError, 'восстановлены'):
            self.save(['new.example.org'], control)
        self.assertEqual(self.config.read_bytes(), ORIGINAL)
        self.assertTrue(control.active)
        self.assertEqual(len(control.calls), 2)

    def test_rollback_failure_is_reported(self):
        control = Control(fail=2)
        with self.assertRaisesRegex(RuntimeError, 'Ошибка применения и восстановления'):
            self.save(['new.example.org'], control)
        self.assertEqual(self.config.read_bytes(), ORIGINAL)

    def test_interrupted_restart_rolls_back(self):
        control = Control()
        actual_ctl = control.ctl
        calls = []
        def ctl(*args):
            calls.append(args)
            if len(calls) == 1:
                raise KeyboardInterrupt
            actual_ctl(*args)
        control.ctl = ctl
        with self.assertRaisesRegex(RuntimeError, 'восстановлены'):
            self.save(['new.example.org'], control)
        self.assertEqual(self.config.read_bytes(), ORIGINAL)

    def test_installer_existing_no_args_preserves_bytes_and_explicit_list_replaces(self):
        source = (ROOT / 'scripts/install-masterdns.sh').read_text()
        block = re.search(r'python3 - "\$SCRIPT_DIR/scripts/masterdns-domains.py".*?<<\x27PY\x27\n(.*?)\nPY', source, re.S).group(1)
        block = block.replace("Path('/etc/masterdns/server_config.toml')", 'Path(' + repr(str(self.config)) + ')')
        args = ['-', str(ROOT / 'scripts/masterdns-domains.py'), '10808', 'xray', '0.0.0.0']
        before_mode = self.config.stat().st_mode
        with mock.patch('sys.argv', args):
            exec(compile(block, 'installer-inline-config', 'exec'), {})
        self.assertEqual(self.config.read_bytes(), ORIGINAL)
        for count in (1, 2, 4):
            values = ['d%d.example.org' % n for n in range(count)]
            with mock.patch('sys.argv', args + values):
                exec(compile(block, 'installer-inline-config', 'exec'), {})
            self.assertEqual(domains.read_domains(self.config.read_text()), values)
            self.assertEqual(self.config.stat().st_mode, before_mode)
            self.assertTrue(self.config.read_bytes().endswith(b'ENCRYPTION_KEY_FILE = "/private/key"\r\n'))

    def test_installer_fresh_multiple_domains_uses_one_config(self):
        source = (ROOT / 'scripts/install-masterdns.sh').read_text()
        block = re.search(r'python3 - "\$SCRIPT_DIR/scripts/masterdns-domains.py".*?<<\x27PY\x27\n(.*?)\nPY', source, re.S).group(1)
        block = block.replace("Path('/etc/masterdns/server_config.toml')", 'Path(' + repr(str(self.config)) + ')')
        self.config.unlink()
        args = ['-', str(ROOT / 'scripts/masterdns-domains.py'), '10808', 'xray', '0.0.0.0', 'd.example.org', 'plain.example.org']
        with mock.patch('sys.argv', args):
            exec(compile(block, 'installer-inline-config', 'exec'), {})
        self.assertEqual(domains.read_domains(self.config.read_text()), args[5:])
        self.assertIn('USE_EXTERNAL_SOCKS5 = true', self.config.read_text())

    def test_failed_atomic_write_restores_and_never_reports_success(self):
        original_writer = domains.atomic_write
        calls = []
        def write(*args):
            calls.append(args)
            if len(calls) == 1:
                raise OSError('disk write failed')
            original_writer(*args)
        with mock.patch.object(domains, 'atomic_write', side_effect=write):
            with self.assertRaises(RuntimeError):
                self.save(['new.example.org'], Control())
        self.assertEqual(self.config.read_bytes(), ORIGINAL)

    def test_external_change_and_invalid_config_never_overwritten(self):
        self.config.write_bytes(ORIGINAL + b'# foreign edit\n')
        with self.assertRaises(ValueError):
            self.save(['new.example.org'], Control())
        self.assertTrue(self.config.read_bytes().endswith(b'# foreign edit\n'))
        with self.assertRaises(ValueError):
            domains.replace_domains('DOMAIN = [', ['new.example.org'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlinks')
    def test_symlink_refused(self):
        target = self.config.with_suffix('.real')
        self.config.rename(target)
        self.config.symlink_to(target)
        with self.assertRaises(ValueError):
            self.save(['new.example.org'], Control())
        self.assertEqual(target.read_bytes(), ORIGINAL)

    def run_menu(self, answers, control=None):
        iterator = iter(answers)
        output = []
        def ask(prompt):
            return next(iterator)
        with mock.patch.object(domains.time, 'sleep', return_value=None):
            # Explicit no-sleep wrapper because save's default is bound at definition.
            real_save = domains.save
            with mock.patch.object(domains, 'save', side_effect=lambda p, d, e, c: real_save(p, d, e, c, sleep=lambda n: None)):
                domains.menu(self.config, control or Control(), ask=ask, output=output.append)
        self.assertIsNone(next(iterator, None))
        return '\n'.join(output)

    def test_menu_add_reorder_delete_discard_does_not_write(self):
        control = Control()
        self.run_menu(['1', 'plain.example.org', '3', '3', '2', '2', '0'], control)
        self.assertEqual(self.config.read_bytes(), ORIGINAL)
        self.assertEqual(control.locks, 0)

    def test_menu_save_preserves_order_without_forced_prefix(self):
        self.run_menu(['1', 'plain.example.org', '3', '3', '2', '2', '5'])
        self.assertEqual(domains.read_domains(self.config.read_text()), ['plain.example.org', 'd1.example.org'])

    def test_menu_last_domain_and_duplicate_rejected(self):
        self.config.write_text('DOMAIN = ["d.example.org"]\n')
        output = self.run_menu(['2', '1', '1', 'D.EXAMPLE.ORG', '0'])
        self.assertIn('от 1 до 16', output)
        self.assertIn('уже есть', output)
        self.assertEqual(domains.read_domains(self.config.read_text()), ['d.example.org'])

    def test_dns_parent_authority_and_no_tunnel_ns_probe(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            answer = 'ns.provider.org.\n' if '+short' in command else 'd.example.org. 300 IN NS tunnel.example.org.\n'
            return subprocess.CompletedProcess(command, 0, stdout=answer)
        with mock.patch.object(domains.shutil, 'which', return_value='/usr/bin/dig'), redirect_stdout(io.StringIO()) as output:
            domains.dns_check(['d.example.org'], run=run)
        self.assertIn('Делегирование видно', output.getvalue())
        self.assertIn('example.org', calls[0])
        self.assertIn('@ns.provider.org', calls[1])
        self.assertIn('+norecurse', calls[1])
        self.assertFalse(any('@tunnel.example.org' in call for call in calls))

    def test_missing_dig_and_timeout_informational(self):
        with mock.patch.object(domains.shutil, 'which', return_value=None), redirect_stdout(io.StringIO()) as output:
            domains.dns_check(['d.example.org'])
        self.assertIn('Ничего не устанавливаем', output.getvalue())
        with mock.patch.object(domains.shutil, 'which', return_value='/usr/bin/dig'), redirect_stdout(io.StringIO()) as output:
            domains.dns_check(['d.example.org'], clock=iter([0, 46]).__next__)
        self.assertIn('неопределённы', output.getvalue())

    def test_actual_export_python_defaults_for_every_preset_and_domain_count(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        program = function.split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        requested_defaults = dict(resolver_balancing_strategy=5, upload_compression_type=2,
                                  download_compression_type=2, rx_tx_workers=16, tunnel_process_workers=0)
        for count in (1, 2, 4):
            expected_domains = ['d%d.example.org' % n for n in range(count)]
            self.config.write_text('DOMAIN = ' + json.dumps(expected_domains) + '\n')
            before = self.config.read_bytes()
            for preset in (1, 2, 3):
                with self.subTest(count=count, preset=preset):
                    env = dict(XM_DNS_KEY='test-key-only', XM_DNS_NAME='Explicit profile name', XM_DNS_TYPE=str(preset), XM_DNS_FORMAT='tuna')
                    args = ['-', str(self.config), str(ROOT / 'scripts/masterdns-domains.py'), str(ROOT / 'scripts/masterdns-link-profiles.py')]
                    with mock.patch.dict(os.environ, env), mock.patch('sys.argv', args), redirect_stdout(io.StringIO()) as output:
                        exec(compile(program, 'actual-export-python', 'exec'), {})
                    payload = decoded_profile(output.getvalue().strip())['profile']
                    self.assertEqual({key: payload[key] for key in requested_defaults}, requested_defaults)
                    self.assertEqual(payload['domains'], expected_domains)
                    self.assertEqual(payload['server']['domain'], expected_domains[0])
                    self.assertEqual(payload['server']['encryption_key'], env['XM_DNS_KEY'])
                    self.assertEqual(payload['name'], env['XM_DNS_NAME'])
                    self.assertEqual(payload['packet_duplication_count'], {1: 4, 2: 2, 3: 1}[preset])
                    self.assertEqual(self.config.read_bytes(), before)

    def test_storm_export_is_one_selected_domain_and_tuna_contains_every_domain(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        program = function.split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        values = ['d.example.org', 'd1.example.org', 'd2.example.org', 'd3.example.org']
        self.config.write_text('DOMAIN = ' + json.dumps(values) + '\nDATA_ENCRYPTION_METHOD = 2\n')
        args = ['-', str(self.config), str(ROOT / 'scripts/masterdns-domains.py'), str(ROOT / 'scripts/masterdns-link-profiles.py')]
        for domain in values:
            for format_name in ('storm', 'tuna'):
                env = dict(XM_DNS_KEY='a' * 32, XM_DNS_NAME='Имя "quoted"', XM_DNS_TYPE='2', XM_DNS_FORMAT=format_name, XM_DNS_DOMAIN=domain)
                with mock.patch.dict(os.environ, env), mock.patch('sys.argv', args), redirect_stdout(io.StringIO()) as output:
                    exec(compile(program, 'actual-export-python', 'exec'), {})
                link = output.getvalue().strip()
                self.assertTrue(link.startswith(format_name + '://' if format_name == 'tuna' else 'stormdns://'))
                profile = decoded_profile(link)['profile']
                self.assertEqual(profile['domains'], values if format_name == 'tuna' else [domain])
                self.assertEqual(profile['server']['domain'], values[0] if format_name == 'tuna' else domain)
                self.assertEqual(profile['server']['encryption_method'], 2)
                if format_name == 'tuna':
                    self.assertNotIn('=', link)

    def test_export_rejects_unknown_format_unknown_domain_and_oversized_inner_profile(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        program = function.split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        args = ['-', str(self.config), str(ROOT / 'scripts/masterdns-domains.py'), str(ROOT / 'scripts/masterdns-link-profiles.py')]
        basic = dict(XM_DNS_KEY='a' * 32, XM_DNS_NAME='name', XM_DNS_TYPE='2', XM_DNS_FORMAT='storm', XM_DNS_DOMAIN='d.example.org')
        for overrides in ({'XM_DNS_FORMAT': 'unknown'}, {'XM_DNS_DOMAIN': 'foreign.example.org'}, {'XM_DNS_NAME': 'x' * 65536}):
            with mock.patch.object(os, 'environ', dict(os.environ) | basic | overrides), mock.patch('sys.argv', args), self.assertRaises(SystemExit), redirect_stdout(io.StringIO()) as output:
                exec(compile(program, 'actual-export-python', 'exec'), {})
            self.assertEqual(output.getvalue(), '')

    def test_export_reads_each_configured_encryption_method(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        program = function.split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        for method in range(6):
            self.config.write_text(f'DOMAIN = ["d.example.org"]\nDATA_ENCRYPTION_METHOD = {method}\n')
            env = dict(XM_DNS_KEY='a' * domains.key_length(method), XM_DNS_NAME='Explicit profile name', XM_DNS_TYPE='2')
            args = ['-', str(self.config), str(ROOT / 'scripts/masterdns-domains.py'), str(ROOT / 'scripts/masterdns-link-profiles.py')]
            with mock.patch.dict(os.environ, env), mock.patch('sys.argv', args), redirect_stdout(io.StringIO()) as output:
                exec(compile(program, 'actual-export-python', 'exec'), {})
            payload = json.loads(base64.b64decode(output.getvalue().strip().removeprefix('stormdns://')))['profile']
            self.assertEqual(payload['server']['encryption_method'], method)
            self.assertEqual(payload['server']['encryption_key'], env['XM_DNS_KEY'])

    def test_saved_client_overrides_are_identical_in_both_export_formats(self):
        module_spec = importlib.util.spec_from_file_location('link_profile_overrides', ROOT / 'scripts/masterdns-link-profiles.py')
        settings = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(settings)
        custom = settings.defaults(2) | dict(resolvers='192.0.2.53:53\n[2001:db8::53]:53',
            resolver_balancing_strategy=8, upload_compression_type=1, download_compression_type=0,
            rx_tx_workers=7, tunnel_process_workers=3, ping_cold_interval_seconds=42)
        settings.save(settings.storage_path(self.config), 2, custom, None)
        stored = settings.storage_path(self.config).read_bytes()
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        program = function.split("<<'PYEOF'\n", 1)[1].split('\nPYEOF', 1)[0]
        args = ['-', str(self.config), str(ROOT / 'scripts/masterdns-domains.py'), str(ROOT / 'scripts/masterdns-link-profiles.py')]
        for format_name in ('storm', 'tuna'):
            env = dict(XM_DNS_KEY='a' * 32, XM_DNS_NAME='Custom profile', XM_DNS_TYPE='2', XM_DNS_FORMAT=format_name, XM_DNS_DOMAIN='d.example.org')
            with mock.patch.dict(os.environ, env), mock.patch('sys.argv', args), redirect_stdout(io.StringIO()) as output:
                exec(compile(program, 'actual-export-python', 'exec'), {})
            payload = decoded_profile(output.getvalue().strip())['profile']
            self.assertEqual({key: payload[key] for key in custom}, custom)
            self.assertEqual(payload['server']['encryption_key'], env['XM_DNS_KEY'])
            self.assertEqual(settings.storage_path(self.config).read_bytes(), stored)
        settings.storage_path(self.config).write_bytes(b'broken JSON')
        with mock.patch.dict(os.environ, env), mock.patch('sys.argv', args), redirect_stdout(io.StringIO()) as output, self.assertRaises(ValueError):
            exec(compile(program, 'actual-export-python', 'exec'), {})
        self.assertEqual(output.getvalue(), '')

    @unittest.skipUnless(os.name == 'posix', 'Linux Bash export menu integration')
    def test_export_menu_emits_one_storm_link_per_domain_or_one_tuna_link(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        generator = 'get_stormdns_link() {' + source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        emitter = 'emit_dns_profile_links() {' + source.split('emit_dns_profile_links() {', 1)[1].split('\ndns_change_domain() {', 1)[0]
        stubs = '''xm_masterdns_domains_helper() { printf '%s\\n' "$HELPER"; }
xm_masterdns_link_profiles_helper() { printf '%s\\n' "$LINK_HELPER"; }
get_dns_key() { printf '%s\\n' "$KEY"; }
get_dns_domains() { printf '%s\\n' "$DOMAINS"; }
'''
        for count in (1, 2, 4, 7):
            expected = ['d%d.example.org' % n for n in range(count)]
            self.config.write_text('DOMAIN = ' + json.dumps(expected) + '\nDATA_ENCRYPTION_METHOD = 2\n')
            for export_format in ('storm', 'tuna'):
                env = dict(os.environ, MASTERDNS_CONFIG_FILE=str(self.config), HELPER=str(ROOT / 'scripts/masterdns-domains.py'),
                    LINK_HELPER=str(ROOT / 'scripts/masterdns-link-profiles.py'), KEY='a' * 32,
                    NAME='Synthetic export', DOMAINS='\n'.join(expected), FORMAT=export_format)
                result = subprocess.run(['bash', '-c', stubs + generator + emitter + '\nemit_dns_profile_links 2 "$NAME" "$FORMAT" no\n'],
                    env=env, capture_output=True, text=True, check=True)
                links = [line for line in result.stdout.splitlines() if line.startswith(('stormdns://', 'tuna://'))]
                self.assertEqual(len(links), count if export_format == 'storm' else 1)
                restored = [decoded_profile(link)['profile']['domains'] for link in links]
                self.assertEqual(restored, [[domain] for domain in expected] if export_format == 'storm' else [expected])

    @unittest.skipUnless(os.name == 'posix', 'Linux Bash settings integration')
    def test_settings_item_five_opens_encryption_menu(self):
        source = (ROOT / 'bin/x-manager').read_text(encoding='utf-8')
        function = 'menu_dns_settings() {' + source.split('menu_dns_settings() {', 1)[1].split('\ninstall_masterdns() {', 1)[0]
        stubs = '''clear() { :; }
get_dns_domain() { echo d.example.org; }
get_dns_key() { echo test-key-only; }
get_dns_profile_name() { echo name; }
get_dns_routing() { echo route; }
get_dns_encryption_name() { echo ChaCha20; }
dns_change_encryption() { echo METHOD_MENU_CALLED; }
'''
        result = subprocess.run(['bash', '-c', stubs + function + '\nmenu_dns_settings\n'], input='5\n0\n', capture_output=True, text=True, check=True)
        self.assertIn('[5]', result.stdout)
        self.assertIn('ChaCha20', result.stdout)
        self.assertEqual(result.stdout.count('METHOD_MENU_CALLED'), 1)

    @unittest.skipUnless(os.name == 'posix', 'Linux Bash export integration')
    def test_real_shell_export_all_domains_and_quote_safe_profile_name(self):
        source = (ROOT / 'bin/x-manager').read_text()
        # The quoted Python heredoc also contains a column-zero closing brace.
        # Bound extraction by the next shell function, not by the first brace.
        function = source.split('get_stormdns_link() {', 1)[1].split('\nshow_single_dns_profile() {', 1)[0]
        function = 'get_stormdns_link() {' + function
        key = 'test-key-only'
        name = 'name "quoted" $(exit 9) \\ end'
        script = 'xm_masterdns_domains_helper() { printf "%s\\n" "$HELPER"; }\nxm_masterdns_link_profiles_helper() { printf "%s\\n" "$LINK_HELPER"; }\n' + function + '\nget_stormdns_link 2 "$DOMAIN" "$KEY" "$NAME" "$FORMAT"\n'
        for count in (1, 2, 4):
            expected = ['d%d.example.org' % n for n in range(count)]
            self.config.write_text('DOMAIN = ' + json.dumps(expected) + '\n')
            env = dict(os.environ, MASTERDNS_CONFIG_FILE=str(self.config), HELPER=str(ROOT / 'scripts/masterdns-domains.py'), LINK_HELPER=str(ROOT / 'scripts/masterdns-link-profiles.py'), KEY=key, NAME=name, DOMAIN=expected[0], FORMAT='tuna')
            result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True, check=True)
            payload = decoded_profile(result.stdout.strip())
            self.assertEqual(payload['profile']['domains'], expected)
            self.assertEqual(payload['profile']['server']['domain'], expected[0])
            self.assertEqual(payload['profile']['server']['encryption_key'], key)
            self.assertEqual(payload['profile']['name'], name)
            self.assertEqual(payload['profile']['resolver_balancing_strategy'], 5)
            self.assertEqual(payload['profile']['upload_compression_type'], 2)
            self.assertEqual(payload['profile']['download_compression_type'], 2)
            self.assertEqual(payload['profile']['rx_tx_workers'], 16)
            self.assertEqual(payload['profile']['tunnel_process_workers'], 0)


class EncryptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'server.toml'
        self.key = self.root / 'custom-key.txt'
        self.original = b'DOMAIN = ["d.example.org", "d1.example.org"]\r\nDATA_ENCRYPTION_METHOD = 5 # keep comment\r\nENCRYPTION_KEY_FILE = "custom-key.txt"\r\nFORWARD_PORT = 10808\r\n'
        self.old_key = b'0123456789abcdef0123456789abcdef\n'
        self.config.write_bytes(self.original)
        self.key.write_bytes(self.old_key)
        self.config.chmod(0o640)
        self.key.chmod(0o600)

    def save(self, method, control=None, **kwargs):
        return domains.save_method(self.config, method, self.original, control or Control(), sleep=lambda n: None, **kwargs)

    def test_method_ids_labels_and_native_key_lengths(self):
        self.assertEqual(len(domains.METHODS), 6)
        self.assertEqual([domains.key_length(n) for n in range(6)], [32, 32, 32, 16, 24, 32])
        self.assertEqual(domains.read_method('DOMAIN=["d.example.org"]'), 1)
        for invalid in (-1, 6, True, '2'):
            with self.assertRaises(ValueError):
                domains.validate_method(invalid)

    def test_compatible_changes_preserve_key_and_all_other_fields(self):
        for method in (0, 1, 2):
            self.config.write_bytes(self.original)
            control = Control()
            self.assertTrue(self.save(method, control))
            self.assertEqual(self.key.read_bytes(), self.old_key)
            self.assertEqual(self.config.read_bytes(), self.original.replace(b'DATA_ENCRYPTION_METHOD = 5', f'DATA_ENCRYPTION_METHOD = {method}'.encode()))
            self.assertEqual(control.calls, [('restart', domains.UNIT)])
            self.assertEqual(control.locks, 1)

    def test_incompatible_key_is_not_silently_regenerated(self):
        for method in (3, 4):
            control = Control()
            with self.assertRaises(ValueError):
                self.save(method, control)
            self.assertEqual(self.key.read_bytes(), self.old_key)
            self.assertEqual(self.config.read_bytes(), self.original)
            self.assertFalse(control.calls)

    def test_explicit_rekey_saves_matching_key_and_preserves_metadata(self):
        config_mode = self.config.stat().st_mode
        key_mode = self.key.stat().st_mode
        for method in (3, 4):
            self.config.write_bytes(self.original)
            self.key.write_bytes(self.old_key)
            control = Control()
            self.assertTrue(self.save(method, control, regenerate=True))
            self.assertEqual(control.calls, [('stop', domains.UNIT), ('restart', domains.UNIT)])
            self.assertEqual(len(self.key.read_bytes()), domains.key_length(method))
            self.assertNotEqual(self.key.read_bytes(), self.old_key)
            self.assertEqual(domains.read_method(self.config.read_text()), method)
            self.assertEqual(self.config.stat().st_mode, config_mode)
            self.assertEqual(self.key.stat().st_mode, key_mode)

    def test_restart_failure_restores_both_files(self):
        control = Control(fail=1)
        with self.assertRaisesRegex(RuntimeError, 'восстановлены'):
            self.save(3, control, regenerate=True)
        self.assertEqual(self.key.read_bytes(), self.old_key)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertTrue(control.active)

    def test_config_write_failure_after_rekey_restores_key(self):
        original_write = domains.atomic_write
        calls = []
        def write(*args):
            calls.append(args[0])
            if len(calls) == 2:
                raise OSError('mock config write error')
            return original_write(*args)
        with mock.patch.object(domains, 'atomic_write', side_effect=write), self.assertRaises(RuntimeError):
            self.save(3, regenerate=True)
        self.assertEqual(self.key.read_bytes(), self.old_key)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_changed_key_or_invalid_current_key_blocks_before_write(self):
        self.key.write_bytes(b'a' * 32)
        with self.assertRaisesRegex(ValueError, 'извне'):
            self.save(2, expected_key=self.old_key)
        self.key.write_bytes(b'bad-length')
        with self.assertRaises(ValueError):
            self.save(3, regenerate=True)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.key.read_bytes(), b'bad-length')

    def test_stopped_service_stays_stopped_after_explicit_rekey(self):
        control = Control(active=False, allowed=False)
        self.assertFalse(self.save(3, control, regenerate=True))
        self.assertFalse(control.calls)
        self.assertEqual(len(self.key.read_bytes()), 16)

    def test_manual_key_update_uses_transaction_and_current_method_length(self):
        self.config.write_bytes(self.original.replace(b'DATA_ENCRYPTION_METHOD = 5', b'DATA_ENCRYPTION_METHOD = 3'))
        self.key.write_bytes(b'a' * 16)
        expected = self.config.read_bytes()
        control = Control(fail=1)
        with self.assertRaises(RuntimeError):
            domains.save_key(self.config, b'b' * 16, expected, control, sleep=lambda n: None)
        self.assertEqual(self.key.read_bytes(), b'a' * 16)
        with self.assertRaises(ValueError):
            domains.save_key(self.config, b'b' * 32, expected, Control(), sleep=lambda n: None)
        self.assertTrue(domains.save_key(self.config, b'b' * 16, expected, Control(), sleep=lambda n: None))
        self.assertEqual(self.key.read_bytes(), b'b' * 16)
        self.assertEqual(self.config.read_bytes(), expected)

    def test_menu_cancel_declines_rekey_without_writes_or_secret_output(self):
        output = []
        answers = iter(['3', 'n'])
        domains.method_menu(self.config, Control(), ask=lambda p: next(answers), output=output.append)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(self.key.read_bytes(), self.old_key)
        self.assertNotIn(self.old_key.decode().strip(), '\n'.join(output))

    def test_menu_explicit_rekey_requires_both_confirmations(self):
        output = []
        answers = iter(['3', 'y', 'y'])
        original_save = domains.save_method
        def save(*args, **kwargs):
            kwargs['sleep'] = lambda n: None
            return original_save(*args, **kwargs)
        with mock.patch.object(domains, 'save_method', side_effect=save):
            domains.method_menu(self.config, Control(), ask=lambda p: next(answers), output=output.append)
        self.assertEqual(domains.read_method(self.config.read_text()), 3)
        self.assertEqual(len(self.key.read_bytes()), 16)
        self.assertNotIn(self.key.read_text(), '\n'.join(output))

    def test_missing_method_inserted_at_top_level_and_shadow_key_refused(self):
        text = 'DOMAIN=["d.example.org"]\n[extra]\nnote="keep"\n'
        updated = domains.replace_method(text, 2)
        self.assertTrue(updated.startswith('DATA_ENCRYPTION_METHOD = 2\n'))
        self.assertTrue(updated.endswith(text))
        with self.assertRaises(ValueError):
            domains.replace_method("NOTE='''\nDATA_ENCRYPTION_METHOD = 5\n'''\nDATA_ENCRYPTION_METHOD = 5\n", 2)


if __name__ == '__main__':
    unittest.main()
