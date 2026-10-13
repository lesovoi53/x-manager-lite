#!/usr/bin/env bash
# Bootstrap the latest complete Lite release; component hashes are pinned inside it.
set -euo pipefail
[ "$EUID" = 0 ] || { echo 'Запустите от root.' >&2; exit 1; }
mode=${1:---quick}
case "$mode" in --quick|--direct|--update) ;; *) echo 'Допустимо: --quick, --direct, --update' >&2; exit 1;; esac
[ "$#" -le 1 ] || exit 1
for command in curl python3 tar; do command -v "$command" >/dev/null || { echo "Нет зависимости: $command" >&2; exit 1; }; done
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
fetch() { curl -fL --proto '=https' --proto-redir '=https' --connect-timeout 15 --max-time 300 --retry 2 "$1" -o "$2"; }
fetch https://api.github.com/repos/lesovoi53/x-manager-lite/releases/latest "$work/release.json"
tag=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tag_name"])' "$work/release.json")
[[ "$tag" =~ ^v[0-9][A-Za-z0-9._-]*$ ]] || { echo 'Некорректная версия выпуска' >&2; exit 1; }
base="https://github.com/lesovoi53/x-manager-lite/releases/download/$tag"
fetch "$base/x-manager.tar.gz" "$work/x-manager.tar.gz"
fetch "$base/SHA256SUMS" "$work/SHA256SUMS"
python3 - "$work" "$tag" <<'PY'
import hashlib,json,pathlib,re,sys,tarfile
root=pathlib.Path(sys.argv[1])
rows=[line.split() for line in (root/'SHA256SUMS').read_text().splitlines()]
hashes=[row[0] for row in rows if len(row)==2 and row[1].lstrip('*')=='x-manager.tar.gz']
if len(hashes)!=1 or not re.fullmatch('[a-f0-9]{64}',hashes[0]) or hashlib.sha256((root/'x-manager.tar.gz').read_bytes()).hexdigest()!=hashes[0]:
    sys.exit('SHA-256 выпуска не совпадает; установка отменена')
with tarfile.open(root/'x-manager.tar.gz') as archive:
    for member in archive.getmembers():
        p=pathlib.PurePosixPath(member.name)
        if p.is_absolute() or '..' in p.parts or not(member.isfile() or member.isdir()):sys.exit('Небезопасный архив')
    archive.extractall(root/'source',**({'filter':'data'} if hasattr(tarfile,'data_filter') else {}))
manifest=json.loads((root/'source/components.json').read_text())
if manifest.get('edition')!='lite' or manifest.get('repository')!='lesovoi53/x-manager-lite' or manifest.get('release')!=sys.argv[2]:
    sys.exit('Неверный состав выпуска Lite')
PY
bash "$work/source/install.sh" "$mode"
