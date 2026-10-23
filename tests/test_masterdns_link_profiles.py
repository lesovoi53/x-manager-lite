import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/masterdns-link-profiles.py'
spec = importlib.util.spec_from_file_location('masterdns_link_profiles', SCRIPT)
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


class LinkProfilesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / 'server_config.toml'
        self.path = self.config.with_name('client_link_profiles.json')
        self.config.write_text('DOMAIN = ["a.test"]\nENCRYPTION_KEY = "unchanged"\n', encoding='utf-8')
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('XM_MASTERDNS_LINK_PROFILES_FILE', None)

    def test_all_original_defaults_exact(self):
        self.assertEqual(profiles.defaults(1), profiles.effective(self.config, 1))
        for preset in (1, 2, 3):
            settings = profiles.defaults(preset)
            self.assertEqual(settings, profiles.validate(settings))
            self.assertEqual(5, settings['resolver_balancing_strategy'])
            self.assertEqual(2, settings['upload_compression_type'])
            self.assertEqual(2, settings['download_compression_type'])
            self.assertEqual(16, settings['rx_tx_workers'])
            self.assertEqual(0, settings['tunnel_process_workers'])
            self.assertEqual(8, settings['max_packets_per_batch'])
            self.assertEqual(600, settings['arq_window_size'])
            self.assertEqual(5, settings['arq_max_rto_seconds'])
            self.assertEqual(11, len(settings['resolvers'].splitlines()))
            self.assertEqual(21, len(settings))
        self.assertEqual((4, 4, True, 28, 200, 60, 900, 2, .05, .5, 2, 15), self.preset_values(1))
        self.assertEqual((2, 2, False, 38, 150, 100, 500, 1, .1, .75, 2, 15), self.preset_values(2))
        self.assertEqual((1, 2, False, 38, 150, 100, 500, 1, .1, .75, 4, 30), self.preset_values(3))
        self.assertFalse(self.path.exists())

    def preset_values(self, preset):
        values = profiles.defaults(preset)
        return tuple(values[key] for key in ('packet_duplication_count', 'setup_packet_duplication_count',
            'base_encode_data', 'min_upload_mtu', 'max_upload_mtu', 'min_download_mtu', 'max_download_mtu',
            'arq_initial_rto_seconds', 'ping_aggressive_interval_seconds', 'ping_lazy_interval_seconds',
            'ping_cooldown_interval_seconds', 'ping_cold_interval_seconds'))

    def test_every_field_exposed_once_and_identity_excluded(self):
        fields = [field for _, group in profiles.GROUPS for field in group]
        self.assertEqual(len(fields), len(set(fields)))
        self.assertEqual(set(profiles.defaults(1)), set(fields))
        for identity in ('name', 'domains', 'server', 'encryption_key', 'encryption_method'):
            self.assertNotIn(identity, fields)

    def test_save_preserves_other_presets_and_server(self):
        original = self.config.read_bytes()
        first = profiles.defaults(1)
        first.update(resolvers='192.0.2.1:5353\n[2001:db8::1]:53', resolver_balancing_strategy=2)
        profiles.save(self.path, 1, first, None)
        second = profiles.defaults(2)
        second['rx_tx_workers'] = 0
        profiles.save(self.path, 2, second, profiles.snapshot(self.path))
        self.assertEqual(first, profiles.effective(self.config, 1))
        self.assertEqual(second, profiles.effective(self.config, 2))
        self.assertEqual(profiles.defaults(3), profiles.effective(self.config, 3))
        self.assertEqual(original, self.config.read_bytes())

    def test_stale_snapshot_refuses_overwrite(self):
        profiles.save(self.path, 1, profiles.defaults(1), None)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'уже изменились'):
            profiles.save(self.path, 2, profiles.defaults(2), None)
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_saved_config_never_falls_back(self):
        for data in (b'garbage', b'{}', b'{"version":2,"profiles":{}}',
                     b'{"version":1,"profiles":{"4":{}}}', b'{"version":1,"profiles":{"1":{}}}',
                     b'{"version":1,"version":1,"profiles":{}}'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    profiles.effective(self.config, 1)
                with self.assertRaises(ValueError):
                    profiles.save(self.path, 1, profiles.defaults(1), data)
                self.assertEqual(data, self.path.read_bytes())

    def test_invalid_types_unknown_fields_and_ranges(self):
        for field, value in (('resolver_balancing_strategy', 9), ('upload_compression_type', 4),
                ('rx_tx_workers', 129), ('tunnel_process_workers', -1), ('packet_duplication_count', 0),
                ('setup_packet_duplication_count', 13), ('base_encode_data', 1), ('arq_window_size', True),
                ('min_upload_mtu', 0), ('max_download_mtu', 65536), ('arq_initial_rto_seconds', math.nan),
                ('ping_cold_interval_seconds', math.inf), ('max_packets_per_batch', 1.5), ('unknown', 1)):
            with self.subTest(field=field, value=value):
                settings = profiles.defaults(1)
                settings[field] = value
                with self.assertRaises(ValueError):
                    profiles.validate(settings)
        for preset in (0, 4, True, '1'):
            with self.assertRaises(ValueError):
                profiles.defaults(preset)

    def test_cross_field_validation(self):
        for field, value in (('min_upload_mtu', 300), ('min_download_mtu', 1000),
                ('setup_packet_duplication_count', 3), ('arq_max_rto_seconds', 1),
                ('ping_lazy_interval_seconds', .02), ('ping_cooldown_interval_seconds', .1),
                ('ping_cold_interval_seconds', 1)):
            with self.subTest(field=field):
                settings = profiles.defaults(1)
                settings[field] = value
                with self.assertRaises(ValueError):
                    profiles.validate(settings)

    def test_numeric_resolvers_ipv4_ipv6_ports(self):
        values = '192.0.2.1\n192.0.2.1:5353\n2001:db8::1\n[2001:db8::1]:5353'
        self.assertEqual(values, profiles.validate_resolvers(values))
        for value in ('', 'example.com', '192.0.2.1:0', '192.0.2.1:65536', '[2001:db8::1]:',
                      '[fe80::1%eth0]:53', '192.0.2.1\n192.0.2.1:53', '2001:db8::1\n[2001:db8::1]:53',
                      '192.0.2.1:５３', '[192.0.2.1]:53', '1.2.3.4\n' * 129):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    profiles.validate_resolvers(value)

    def test_environment_path_override(self):
        alternate = Path(self.directory.name) / 'alternate.json'
        os.environ['XM_MASTERDNS_LINK_PROFILES_FILE'] = str(alternate)
        self.assertEqual(alternate, profiles.storage_path(self.config))
        changed = profiles.defaults(3)
        changed['resolver_balancing_strategy'] = 1
        profiles.save(alternate, 3, changed, None)
        self.assertEqual(changed, profiles.effective(self.config, 3))

    def test_cli_effective_json_and_error_exit(self):
        command = [sys.executable, str(SCRIPT), '--config', str(self.config), 'effective', '2']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(profiles.defaults(2), json.loads(result.stdout))
        self.path.write_bytes(b'invalid')
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual(b'', result.stdout)

    def test_discard_and_reset_do_not_write_until_save(self):
        changed = profiles.defaults(1)
        changed['rx_tx_workers'] = 3
        profiles.save(self.path, 1, changed, None)
        before = self.path.read_bytes()
        answers = iter(('r', 'p', '0'))
        with patch('builtins.print'):
            self.assertFalse(profiles.menu(self.config, 1, lambda _: next(answers)))
        self.assertEqual(before, self.path.read_bytes())
        answers = iter(('r', 's'))
        with patch('builtins.print'):
            self.assertTrue(profiles.menu(self.config, 1, lambda _: next(answers)))
        self.assertEqual(profiles.defaults(1), profiles.effective(self.config, 1))

    def test_menu_edits_and_saves_worker(self):
        answers = iter(('4', '1', '0', 's'))
        with patch('builtins.print'):
            self.assertTrue(profiles.menu(self.config, 2, lambda _: next(answers)))
        self.assertEqual(0, profiles.effective(self.config, 2)['rx_tx_workers'])

    def test_failed_replace_preserves_saved_file(self):
        profiles.save(self.path, 1, profiles.defaults(1), None)
        before = self.path.read_bytes()
        with patch.object(profiles.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                profiles.save(self.path, 2, profiles.defaults(2), before)
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(list(self.path.parent.glob('.masterdns-link-*')))

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions and symlinks')
    def test_permissions_owner_and_symlink_rejection(self):
        profiles.save(self.path, 1, profiles.defaults(1), None)
        before = self.path.stat()
        os.chmod(self.path, 0o644)
        profiles.save(self.path, 2, profiles.defaults(2), self.path.read_bytes())
        after = self.path.stat()
        self.assertEqual(0o600, after.st_mode & 0o777)
        self.assertEqual((before.st_uid, before.st_gid), (after.st_uid, after.st_gid))
        link = self.path.with_name('symlink.json')
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            profiles.snapshot(link)
        with self.assertRaises(ValueError):
            profiles.save(link, 1, profiles.defaults(1), self.path.read_bytes())

    @unittest.skipUnless(os.name == 'posix', 'POSIX concurrent writer lock')
    def test_concurrent_writers_cannot_overwrite_stale_snapshot(self):
        code = '''import importlib.util,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('profiles',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
try:
 module.save(Path(sys.argv[2]),int(sys.argv[3]),module.defaults(int(sys.argv[3])),None)
except ValueError:
 sys.exit(3)
'''
        children = [subprocess.Popen([sys.executable, '-c', code, str(SCRIPT), str(self.path), str(preset)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for preset in (1, 2)]
        results = []
        for child in children:
            _, error = child.communicate(timeout=10)
            self.assertEqual(b'', error)
            results.append(child.returncode)
        self.assertEqual([0, 3], sorted(results))
        self.assertEqual(1, len(profiles.decode(self.path.read_bytes())['profiles']))


if __name__ == '__main__':
    unittest.main()
