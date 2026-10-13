#!/usr/bin/env python3
"""Persist X-Manager INPUT rules within UFW, without changing foreign policy.

Only callers in this distribution use this adapter. Other iptables tables and
inactive UFW retain their existing behavior. Managed rules precede UFW's input
rules, and are restored by normal UFW reload and boot processing.
"""
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

BEGIN = '# BEGIN X-MANAGER LITE INPUT\n'
END = '# END X-MANAGER LITE INPUT\n'


def active(root=Path('/')):
    path = root / 'etc/ufw/ufw.conf'
    return path.exists() and setting(path.read_text(), 'ENABLED') == 'yes'


def setting(text, key):
    for line in text.splitlines():
        tokens = shlex.split(line, comments=True)
        joined = ''.join(tokens)
        if joined.startswith(key + '='):
            return joined.split('=', 1)[1]
    return None


def identity(rule):
    result = list(rule)
    if '--comment' in result:
        index = result.index('--comment')
        del result[index:index+2]
    if '-m' in result and result[result.index('-m')+1] == 'comment':
        index = result.index('-m')
        del result[index:index+2]
    return result


def split_rules(text):
    if BEGIN not in text:
        if END in text:
            raise ValueError('Invalid managed UFW marker')
        return text, []
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError('Invalid managed UFW markers')
    before, remainder = text.split(BEGIN)
    body, after = remainder.split(END)
    rules = []
    for line in body.splitlines():
        tokens = shlex.split(line)
        if len(tokens) < 2 or tokens[0] != '-A' or tokens[1] not in ('ufw-before-input', 'ufw6-before-input'):
            raise ValueError('Unexpected rule inside managed UFW block')
        rules.append(tokens[2:])
    return before + after, rules


def render(text, rules):
    clean, _ = split_rules(text)
    lines = clean.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == '*filter')
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == 'COMMIT')
        point = next(i for i in range(start + 1, end) if lines[i].startswith('-'))
    except StopIteration as error:
        raise ValueError('Unsupported UFW before.rules format') from error
    chain = next((name for name in ('ufw-before-input', 'ufw6-before-input')
                  if any(line.startswith(':' + name + ' ') for line in lines[start:end])), None)
    if chain is None:
        raise ValueError('Missing UFW input chain')
    if rules:
        body = ''.join('-A ' + chain + ' ' + quote_rule(rule) + '\n' for rule in rules)
        lines.insert(point, BEGIN + body + END)
    return ''.join(lines)


def quote_rule(rule):
    # iptables-restore accepts double quotes, not shell single-quote escaping.
    return ' '.join('"' + item.replace('\\', '\\\\').replace('"', '\\"') + '"'
                    if index and rule[index-1] == '--comment' else item
                    for index, item in enumerate(rule))


def validate(rule):
    index = 0
    allowed = {'-p', '--dport', '-i', '-m', '--comment', '-j'}
    values = {}
    while index < len(rule):
        key = rule[index]
        if key == '!':
            if rule[index:index+3] != ['!', '-i', 'lo']:
                raise ValueError('Unsupported inverted UFW input rule')
            index += 3
            continue
        if key not in allowed or index + 1 >= len(rule):
            raise ValueError('Unsupported managed UFW input rule')
        value = rule[index+1]
        if not re.fullmatch(r'[A-Za-z0-9_.:@+-]+', value):
            raise ValueError('Invalid managed firewall value')
        values[key] = value
        index += 2
    if values.get('-p') not in ('tcp', 'udp') or values.get('-j') not in ('ACCEPT', 'DROP'):
        raise ValueError('Only TCP/UDP ACCEPT/DROP input rules are supported')
    ports = values.get('--dport', '').split(':')
    if len(ports) not in (1, 2) or not all(p.isdigit() and 1 <= int(p) <= 65535 for p in ports):
        raise ValueError('Invalid managed firewall port')
    if int(ports[0]) > int(ports[-1]):
        raise ValueError('Reversed firewall port range')
    if '-m' in values and values['-m'] != 'comment':
        raise ValueError('Unsupported managed firewall match')
    return values, (int(ports[0]), int(ports[-1]))


def protect_ssh(values, ports, run=subprocess.run):
    if values['-j'] != 'DROP' or values['-p'] != 'tcp':
        return
    result = run(['ss', '-H', '-ltnp'], capture_output=True, text=True, check=True)
    protected = {22}
    for line in result.stdout.splitlines():
        if 'sshd' in line or '"systemd"' in line:
            fields = line.split()
            if len(fields) >= 4 and fields[3].rsplit(':', 1)[-1].isdigit():
                protected.add(int(fields[3].rsplit(':', 1)[-1]))
    connection = os.environ.get('SSH_CONNECTION', '').split()
    if len(connection) == 4 and connection[-1].isdigit():
        protected.add(int(connection[-1]))
    if any(ports[0] <= port <= ports[1] for port in protected):
        raise ValueError('Refusing firewall DROP overlapping SSH or systemd socket port')


def protect_direct_accept(values, ports, run=subprocess.run):
    if values['-j'] != 'DROP':
        return
    for tool in ('iptables', 'ip6tables'):
        lines = run([tool, '-S', 'INPUT'], capture_output=True, text=True, check=True).stdout.splitlines()
        for line in lines:
            tokens = shlex.split(line)
            if tokens[:2] != ['-A', 'INPUT'] or '-j' not in tokens or tokens[tokens.index('-j')+1] != 'ACCEPT':
                continue
            def value(key):
                return tokens[tokens.index(key)+1] if key in tokens else None
            if value('-p') not in (None, values['-p']):
                continue
            if value('-i') and values.get('-i') and value('-i') != values['-i']:
                continue
            raw = value('--dport')
            if raw and re.fullmatch(r'[0-9]+(:[0-9]+)?', raw):
                span = list(map(int, raw.split(':')))
                if span[-1] < ports[0] or span[0] > ports[-1]:
                    continue
            raise ValueError('Direct INPUT ACCEPT may bypass this UFW block; inspect existing firewall policy before closing the port')


def atomic(path, text):
    fd, name = tempfile.mkstemp(prefix='.x-manager-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def update(paths, rules, run=subprocess.run):
    originals = {path: path.read_text() for path in paths}
    updated = {path: render(text, rules) for path, text in originals.items()}
    snapshots = {}
    for tool in ('iptables', 'ip6tables'):
        snapshots[tool] = run([tool+'-save'], capture_output=True, text=True, check=True).stdout
    try:
        for path, text in updated.items():
            atomic(path, text)
        run(['ufw', 'reload'], capture_output=True, text=True, check=True)
        # Adopt matching legacy Lite rules after persisting their equivalent.
        # Older installations put these before UFW's jumps, where they would
        # bypass a later managed DROP. Preserve other ports/foreign comments.
        for tool in ('iptables', 'ip6tables'):
            current = run([tool, '-S', 'INPUT'], capture_output=True, text=True, check=True).stdout
            for line in current.splitlines():
                tokens = shlex.split(line)
                if tokens[:2] != ['-A', 'INPUT']:
                    continue
                legacy = tokens[2:]
                comment = legacy[legacy.index('--comment')+1] if '--comment' in legacy else ''
                if comment and not comment.startswith(('XM_SNELL6_', 'SNELL_', 'MIERU_', 'WDTT_', 'CSQTT_')):
                    continue
                # iptables -S adds redundant protocol matches, absent in calls.
                normalized = list(legacy)
                for proto in ('tcp', 'udp'):
                    while '-m' in normalized:
                        positions = [i for i in range(len(normalized)-1) if normalized[i:i+2] == ['-m', proto]]
                        if not positions: break
                        i = positions[0]; del normalized[i:i+2]
                try:
                    validate(normalized)
                except ValueError:
                    continue
                if any(identity(normalized) == identity(rule) for rule in rules):
                    run([tool, '-w', '-D', 'INPUT', *legacy], capture_output=True, text=True, check=True)
    except Exception as error:
        failures = []
        for path, text in originals.items():
            try:
                atomic(path, text)
            except Exception as restore_error:
                failures.append(str(restore_error))
        # Restore the exact live rules too, even if the UFW reload failed halfway.
        for tool, snapshot in snapshots.items():
            try:
                run([tool+'-restore'], input=snapshot, text=True, capture_output=True, check=True)
            except Exception as restore_error:
                failures.append(str(restore_error))
        if failures:
            raise RuntimeError('Firewall rollback incomplete: ' + '; '.join(failures)) from error
        raise


def main(args):
    if not active():
        return subprocess.run(['iptables', *args]).returncode
    clean = list(args)
    if clean[:1] == ['-w']:
        clean.pop(0)
        if clean and clean[0].isdigit():
            clean.pop(0)
    if len(clean) < 2 or clean[1] != 'INPUT':
        return subprocess.run(['iptables', *args]).returncode
    if not shutil.which('ufw'):
        raise ValueError('UFW enabled but ufw command unavailable')
    action = clean[0]
    if action not in ('-C', '-I', '-A', '-D', '-S', '-R'):
        raise ValueError('Unsupported INPUT command with active UFW')
    import fcntl
    with open('/run/lock/x-manager-ufw.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        paths = [Path('/etc/ufw/before.rules')]
        defaults = Path('/etc/default/ufw').read_text()
        if setting(defaults, 'IPV6') == 'yes':
            paths.append(Path('/etc/ufw/before6.rules'))
        _, rules = split_rules(paths[0].read_text())
        if action == '-S':
            for rule in rules:
                print('-A INPUT ' + quote_rule(rule))
            return 0
        rule = clean[2:]
        position = None
        if action == '-R':
            if not rule or not rule[0].isdigit() or not 1 <= int(rule[0]) <= len(rules):
                raise ValueError('Invalid owned UFW rule position')
            position = int(rule.pop(0)) - 1
        if action == '-I' and rule[:1] == ['1']:
            rule.pop(0)
        values, ports = validate(rule)
        matching = [item for item in rules if identity(item) == identity(rule)]
        if action == '-C':
            return 0 if matching else 1
        if action == '-D':
            if not matching:
                return 1  # Never remove a foreign rule, even if it looks identical.
            rules = [item for item in rules if item not in matching]
        else:
            protect_ssh(values, ports)
            protect_direct_accept(values, ports)
            if position is not None:
                rules[position] = rule
            elif matching:
                return 0
            else:
                rules.insert(0 if action == '-I' else len(rules), rule)
        update(paths, rules)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as error:
        print('X-Manager firewall failed: ' + str(error), file=sys.stderr)
        sys.exit(2)
