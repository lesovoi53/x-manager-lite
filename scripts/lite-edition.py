#!/usr/bin/env python3
"""Edition boundary and maintenance actions. Never convert Full implicitly."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPOSITORY = 'lesovoi53/x-manager-lite'


def check(root=Path('/')):
    manifest = root / 'usr/local/share/x-manager/components.json'
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if old.get('repository') != REPOSITORY or old.get('edition') != 'lite':
            raise ValueError('Обнаружен полный X-Manager. Переход Full → Lite требует отдельной миграции; файлы не изменены.')
    elif (root / 'usr/local/bin/x-manager').exists():
        raise ValueError('Обнаружен X-Manager без манифеста Lite; автоматическая замена запрещена.')


def controller():
    spec = importlib.util.spec_from_file_location('lite_control', Path(__file__).with_name('service-control.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Controller()


def restart_active(control=None):
    control = control or controller()
    units = ['tuna-subscriptions', 'snell', 'mita', 'masterdns', 'wdtt', 'csqtt']
    units += ['snell6@'+str(i) for i in range(1,9)]
    for unit in units:
        state = control.status(unit)
        if state.get('ActiveState') == 'active' and control.allowed(unit):
            result = control.change('restart', unit)
            if result.get('ActiveState') != 'active':
                raise RuntimeError('Служба не восстановилась: '+unit)
            print(unit+': работает')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','restart-active'])
    args=parser.parse_args()
    try:
        if args.action == 'check': check()
        else: restart_active()
    except (ValueError, OSError, RuntimeError) as error:
        sys.exit('X-Manager Lite: '+str(error))
