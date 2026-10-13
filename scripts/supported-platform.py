#!/usr/bin/env python3
"""Shared, read-only platform gate for the installer and Snell endpoint manager."""
from pathlib import Path
import platform
import shlex
import sys

SUPPORTED = {('debian', '12'), ('debian', '13'), ('ubuntu', '24.04'), ('ubuntu', '26.04')}


def validate(text, machine):
    values = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, raw = line.split('=', 1)
        if key not in ('ID', 'VERSION_ID'):
            continue
        parts = shlex.split(raw, comments=True)
        if len(parts) != 1 or key in values:
            raise ValueError('Invalid os-release identity')
        values[key] = parts[0]
    identity = (values.get('ID'), values.get('VERSION_ID'))
    if identity not in SUPPORTED:
        raise ValueError('Supported OS: Debian 12/13 or Ubuntu 24.04/26.04 LTS; other releases are not validated')
    if machine not in ('x86_64', 'amd64'):
        raise ValueError('Supported architecture: amd64 (x86_64)')
    return identity


def check():
    return validate(Path('/etc/os-release').read_text(), platform.machine())


if __name__ == '__main__':
    try:
        check()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
