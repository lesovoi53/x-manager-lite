import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ports = load('plan-ports')
gateways = load('detect-gateways')



class PortsAndGateways(unittest.TestCase):
    def test_busy_preferred_port_and_forbidden_ports(self):
        with socket.socket() as listener:
            listener.bind(('0.0.0.0', 0))
            busy = listener.getsockname()[1]
            selected = ports.choose(busy, set())
            self.assertNotEqual(selected, busy)
            self.assertNotIn(selected, ports.FORBIDDEN)
        for forbidden in (443, 8443):
            self.assertNotEqual(ports.choose(forbidden, set()), forbidden)

    def test_new_port_plan_is_unique_and_permitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = ports.plan(Path(tmp))
            actual = [v for v in plan.values() if isinstance(v, int)]
            self.assertEqual(len(actual), len(set(actual)))
            self.assertFalse(set(actual) & ports.FORBIDDEN)

    def test_gateway_conflict_is_atomic_and_repeat_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'xui.db')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE inbounds (id INTEGER PRIMARY KEY, user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)')
                db.execute('CREATE TABLE settings (key, value)')
                db.execute('INSERT INTO settings VALUES (?, ?)', ('xrayTemplateConfig', '{"routing":{"balancers":[{"tag":"keep","fallbackTag":"custom"}]}}'))
                db.execute("INSERT INTO inbounds (port, protocol, settings, stream_settings, tag, enable) VALUES (12346,'vless','{\"clients\":[{\"id\":\"fixture-uuid\"}]}','{}','customer',1)")
            with self.assertRaises(ValueError):
                gateways.configure(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM inbounds').fetchone()[0], 1)
                db.execute('UPDATE inbounds SET port=23456 WHERE tag="customer"')
            gateways.configure(path)
            with sqlite3.connect(path) as db:
                before = list(db.iterdump())
            gateways.configure(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(list(db.iterdump()), before)
                self.assertEqual(json.loads(db.execute('SELECT value FROM settings').fetchone()[0])['routing']['balancers'][0]['fallbackTag'], 'custom')


if __name__ == '__main__':
    unittest.main()
