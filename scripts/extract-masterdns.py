#!/usr/bin/env python3
"""Extract just the expected executable from an already hash-verified archive."""
from pathlib import Path, PurePosixPath
import sys
import tarfile

with tarfile.open(sys.argv[1]) as archive:
    candidates=[m for m in archive.getmembers() if m.isfile() and PurePosixPath(m.name).name.startswith('MasterDnsVPN_Server_Linux_')]
    if len(candidates)!=1:
        sys.exit('Нет однозначного бинарника MasterDNS в архиве')
    data=archive.extractfile(candidates[0]).read()
    if data[:6]!=b'\x7fELF\x02\x01' or data[18:20]!=b'\x3e\x00':
        sys.exit('Бинарник MasterDNS не соответствует Linux amd64')
    target=Path(sys.argv[2]);target.write_bytes(data);target.chmod(0o755)
