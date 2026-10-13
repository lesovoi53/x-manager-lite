import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('firewall_adapter', Path(__file__).resolve().parents[1]/'scripts/firewall-adapter.py')
fw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fw)
BEFORE = '*filter\n:ufw-before-input - [0:0]\n-A ufw-before-input -i lo -j ACCEPT\nCOMMIT\n'
ALLOW = ['-p', 'tcp', '--dport', '23456', '-m', 'comment', '--comment', 'XM_SNELL6_example', '-j', 'ACCEPT']


class FirewallAdapter(unittest.TestCase):
    def test_quoted_ufw_configuration(self):
        self.assertEqual(fw.setting('ENABLED="yes" # active\n', 'ENABLED'), 'yes')
        self.assertEqual(fw.setting("IPV6 = 'yes'\n", 'IPV6'), 'yes')

    def test_port_identity_ignores_only_managed_comments(self):
        self.assertEqual(fw.identity(ALLOW), ['-p', 'tcp', '--dport', '23456', '-j', 'ACCEPT'])
        self.assertNotEqual(fw.identity(ALLOW), fw.identity(['-p', 'tcp', '--dport', '23456', '-j', 'DROP']))

    def test_render_preserves_foreign_rules_and_is_idempotent(self):
        output = fw.render(BEFORE, [ALLOW])
        self.assertEqual(fw.split_rules(output), (BEFORE, [ALLOW]))
        self.assertEqual(fw.render(output, [ALLOW]), output)
        self.assertEqual(fw.render(output, []), BEFORE)
        self.assertLess(output.index('--dport'), output.index('-i lo'))

    def test_ipv6_chain_uses_ufw6_name(self):
        before = BEFORE.replace('ufw-before-input', 'ufw6-before-input')
        output = fw.render(before, [ALLOW])
        self.assertEqual(fw.split_rules(output), (before, [ALLOW]))
        self.assertIn('-A ufw6-before-input -p tcp', output)

    def test_malformed_block_and_unsupported_rules_fail(self):
        for text in (BEFORE+fw.END, BEFORE+fw.BEGIN+fw.BEGIN+fw.END):
            with self.assertRaises(ValueError):
                fw.split_rules(text)
        for rule in (['-j', 'ACCEPT'], ['-p', 'tcp', '--dport', '0', '-j', 'DROP'],
                     ['-p', 'tcp', '--dport', '20:10', '-j', 'ACCEPT']):
            with self.assertRaises(ValueError):
                fw.validate(rule)

    def test_ssh_custom_port_drop_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, stdout='LISTEN 0 128 0.0.0.0:22022 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n')
        values, ports = fw.validate(['-p', 'tcp', '--dport', '22000:23000', '-j', 'DROP'])
        with self.assertRaisesRegex(ValueError, 'SSH'):
            fw.protect_ssh(values, ports, lambda *a, **k: result)

    def test_direct_accept_cannot_silently_bypass_managed_close(self):
        result = subprocess.CompletedProcess([], 0, stdout='-A INPUT -p tcp --dport 23456 -j ACCEPT\n')
        values, ports = fw.validate(['-p', 'tcp', '--dport', '23456', '-j', 'DROP'])
        with self.assertRaisesRegex(ValueError, 'bypass'):
            fw.protect_direct_accept(values, ports, lambda *a, **k: result)

    def test_reload_failure_restores_persistent_and_live_rules(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args == ['ufw', 'reload']:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout='saved-'+args[0])
        with tempfile.TemporaryDirectory() as directory:
            files = [Path(directory)/name for name in ('before.rules', 'before6.rules')]
            for file in files:
                file.write_text(BEFORE)
            with self.assertRaises(subprocess.CalledProcessError):
                fw.update(files, [ALLOW], run)
            self.assertEqual([file.read_text() for file in files], [BEFORE, BEFORE])
        restores = [(args[0], kwargs['input']) for args, kwargs in calls if args[0].endswith('-restore')]
        self.assertEqual(restores, [('iptables-restore', 'saved-iptables-save'), ('ip6tables-restore', 'saved-ip6tables-save')])

    def test_inactive_ufw_passes_original_command_through(self):
        with patch.object(fw, 'active', return_value=False), patch.object(fw.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(fw.main(['-C', 'INPUT', *ALLOW]), 1)
            run.assert_called_once_with(['iptables', '-C', 'INPUT', *ALLOW])

    def test_matching_legacy_rule_migrated_foreign_comment_preserved(self):
        calls = []
        legacy = '-A INPUT -p tcp -m tcp --dport 23456 -j ACCEPT\n'
        foreign = '-A INPUT -p tcp --dport 23456 -m comment --comment administrator -j ACCEPT\n'
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=legacy+foreign if args[1:] == ['-S', 'INPUT'] else '')
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'before.rules'; file.write_text(BEFORE)
            fw.update([file], [ALLOW], run)
        deletes = [args for args in calls if '-D' in args]
        self.assertEqual(len(deletes), 2)
        self.assertTrue(all('administrator' not in args for args in deletes))

    def test_rollback_attempts_ipv6_even_when_ipv4_restore_fails(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args in (['ufw', 'reload'], ['iptables-restore']):
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0, stdout='saved')
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'before.rules'
            file.write_text(BEFORE)
            with self.assertRaisesRegex(RuntimeError, 'rollback incomplete'):
                fw.update([file], [ALLOW], run)
            self.assertEqual(file.read_text(), BEFORE)
        self.assertIn(['ip6tables-restore'], calls)


if __name__ == '__main__':
    unittest.main()
