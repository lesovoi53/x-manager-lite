"""Real packets through active UFW; disposable marked VM only."""
import hashlib
import http.server
from pathlib import Path
import subprocess
import threading

assert Path('/.x-manager-test-lab').exists(), 'Disposable VM only'
TOOL = str(Path(__file__).resolve().parents[1]/'scripts/firewall-adapter.py')
NS = 'xm-lite-ufw-test'


def run(*args, ok=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == ok, (args[:3], result.returncode, result.stderr)
    return result.stdout


def rule(action, target):
    return run('python3', TOOL, action, 'INPUT', '-p', 'tcp', '--dport', '23456', '-j', target)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204); self.end_headers()

    def log_message(self, *args):
        pass


assert 'Status: active' in run('ufw', 'status')
files = [Path('/etc/ufw')/p for p in ('user.rules', 'user6.rules')]
before = [p.read_bytes() for p in files]
server = http.server.HTTPServer(('0.0.0.0', 23456), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
run('ip', 'netns', 'add', NS)
try:
    run('ip', 'link', 'add', 'xmufw0', 'type', 'veth', 'peer', 'name', 'xmufw1')
    run('ip', 'link', 'set', 'xmufw1', 'netns', NS)
    run('ip', 'addr', 'add', '10.223.11.1/24', 'dev', 'xmufw0')
    run('ip', 'link', 'set', 'xmufw0', 'up')
    run('ip', '-n', NS, 'addr', 'add', '10.223.11.2/24', 'dev', 'xmufw1')
    run('ip', '-n', NS, 'link', 'set', 'xmufw1', 'up')
    curl = ('ip', 'netns', 'exec', NS, 'curl', '-fsS', '--max-time', '2', '-o', '/dev/null', 'http://10.223.11.1:23456/')
    run(*curl, ok=False)
    rule('-I', 'ACCEPT'); run(*curl)
    run('ufw', 'reload'); run(*curl)
    rule('-I', 'DROP'); run(*curl, ok=False)
    run('ufw', 'reload'); run(*curl, ok=False)
    rule('-D', 'DROP'); run(*curl)
    rule('-D', 'ACCEPT'); run(*curl, ok=False)
    run('python3', TOOL, '-I', 'INPUT', '-p', 'tcp', '--dport', '22', '-j', 'DROP', ok=False)
    assert [p.read_bytes() for p in files] == before
    print('UFW real packets: default deny, allow, reload, close, reopen, remove, SSH guard; foreign user rules unchanged: PASS')
finally:
    # Cleanup only the test rules/network, never a user rule or other namespace.
    for target in ('DROP', 'ACCEPT'):
        subprocess.run(['python3', TOOL, '-D', 'INPUT', '-p', 'tcp', '--dport', '23456', '-j', target], capture_output=True)
    # Deleting the namespace can also remove its veth peer asynchronously.
    # Delete the pair first so cleanup is deterministic across kernel versions.
    subprocess.run(['ip', 'link', 'del', 'xmufw0'], check=True)
    subprocess.run(['ip', 'netns', 'del', NS], check=True)
    server.shutdown(); server.server_close()
