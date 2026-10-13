import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('supported_platform', ROOT/'scripts/supported-platform.py')
platform = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform)


class SupportedPlatform(unittest.TestCase):
    def test_supported_releases_and_quoting(self):
        for distro, version in [('debian', '12'), ('debian', '13'), ('ubuntu', '24.04'), ('ubuntu', '26.04')]:
            for arch in ['amd64', 'x86_64']:
                self.assertEqual(platform.validate(f'ID={distro}\nVERSION_ID="{version}"\n', arch), (distro, version))

    def test_no_implicit_derivative_or_future_release_support(self):
        for text in ['ID=ubuntu\nVERSION_ID=22.04', 'ID=ubuntu\nVERSION_ID=26.10',
                     'ID=linuxmint\nID_LIKE=ubuntu\nVERSION_ID=24.04',
                     'ID=debian\nVERSION_ID=14', 'ID=debian',
                     'ID=debian\nID=ubuntu\nVERSION_ID=24.04',
                     'ID="$(touch /tmp/not-executed)"\nVERSION_ID=24.04']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                platform.validate(text, 'x86_64')

    def test_arm_rejected(self):
        with self.assertRaises(ValueError):
            platform.validate('ID=ubuntu\nVERSION_ID=24.04', 'aarch64')

    def test_both_entrypoints_use_shared_gate(self):
        self.assertIn('scripts/supported-platform.py', (ROOT/'scripts/installer-common.sh').read_text())
        self.assertIn("load_helper('supported-platform').check()", (ROOT/'scripts/snell6-endpoints.py').read_text())
        self.assertIn('menu-lite.sh,supported-platform.py}', (ROOT/'install.sh').read_text())
