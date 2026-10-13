#!/usr/bin/env python3
"""Package a clean committed Lite checkout and verified, unchanged component assets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    def git(*a):return subprocess.check_output(['git','-C',str(root),*a]).decode('utf-8').strip()
    if git('status','--porcelain'):raise SystemExit('Commit the reviewed changes before building a release')
    manifest=json.loads((root/'components.json').read_text())
    if manifest.get('edition')!='lite' or manifest.get('repository')!='lesovoi53/x-manager-lite':
        raise SystemExit('Wrong release edition')
    for name,expected in manifest['sha256'].items():
        if Path(name).name!=name or sha(args.assets/name)!=expected:raise SystemExit('Asset mismatch: '+name)
    out=args.output.resolve()
    if out==root or root in out.parents:raise SystemExit('Output must be outside the source checkout')
    out.mkdir(parents=True,exist_ok=True)
    names=git('ls-files','-z').split('\0')
    stamp=int(git('show','-s','--format=%ct','HEAD'))
    allowed={'bin','scripts','systemd','tests','tuna-sub-server','licenses','docs'}
    singles={'install.sh','components.json','LICENSE','README.md','ATTRIBUTION.md'}
    with tarfile.open(out/'x-manager.tar.gz','w:gz',format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(n for n in names if n):
            p=Path(name)
            if p.parts[0] not in allowed and name not in singles:continue
            path=root/p
            if path.is_symlink() or not path.is_file():raise SystemExit('Unexpected source member: '+name)
            info=archive.gettarinfo(str(path),name)
            info.uid=info.gid=0;info.uname=info.gname='root';info.mtime=stamp
            info.mode=0o755 if path.suffix=='.sh' or p.parts[0]=='bin' else 0o644
            with path.open('rb') as stream:archive.addfile(info,stream)
    files=['x-manager.tar.gz','quick-start.sh',*sorted(manifest['sha256'])]
    shutil.copyfile(root/'scripts/quick-start.sh',out/'quick-start.sh')
    for name in manifest['sha256']:shutil.copyfile(args.assets/name,out/name)
    (out/'SHA256SUMS').write_text(''.join(sha(out/name)+'  '+name+'\n' for name in files),encoding='ascii')
    print('Release '+manifest['release']+': '+str(len(files)+1)+' files in '+str(out))

if __name__=='__main__':main()
