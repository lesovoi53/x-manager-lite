#!/usr/bin/env python3
"""Opt-in TCP profile. Preview, verified apply and conflict-safe rollback."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('tcp_network_base',Path(__file__).with_name('network-profile.py'))
base=importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.CONFIG='/etc/sysctl.d/91-tuna-tcp.conf'
base.STATE='/var/lib/x-manager/tcp-profile/snapshot.json'
base.HEADER='# Managed by X-Manager Lite tcp-profile.py\n'
KEYS={'net.ipv4.tcp_notsent_lowat':16384,'net.ipv4.tcp_slow_start_after_idle':0,'net.ipv4.tcp_fastopen':3}
base.ADAPTIVE=KEYS
base.VALUES={}
base.NOTE='TCP-настройки; UDP не меняется. TFO требует поддержки приложения.'


class Profile(base.Profile):
    def effective(self,keys=None):
        return super().effective(KEYS if keys is None else keys)

    def plan(self,adaptive=False,selected=None):
        selected=selected or []
        current=self.effective()
        desired={k:str(int(current[k])|3 if k.endswith('tcp_fastopen') else KEYS[k]) for k in selected}
        declarations=self.source_declarations(desired)
        blockers=[]
        if not desired:blockers.append('Выберите хотя бы один параметр.')
        for row in declarations:
            if not row['superseded'] and row['potential_later_override'] and row['value']!=desired[row['key']]:
                blockers.append('Конфликт: '+row['file']+':'+str(row['line']))
        target=self.path(base.CONFIG)
        if target.is_symlink() or (target.exists() and not self.read(base.CONFIG).startswith(base.HEADER)):
            blockers.append('Файл принадлежит другой конфигурации; замена запрещена.')
        if self.path(base.STATE).exists() or self.path(base.STATE).is_symlink():
            blockers.append('Профиль уже применялся. Для другого набора сначала выполните откат.')
        return {'current':current,'desired':desired,'ready':not blockers,'blockers':blockers}

    def load_snapshot(self):
        if self.path(base.STATE).is_symlink():raise ValueError('Недопустимая копия TCP-профиля')
        data=json.loads(self.read(base.STATE))
        before=data.get('before',{}); desired=data.get('desired',{})
        if not desired or not isinstance(desired,dict) or not isinstance(before,dict) or set(before)!=set(desired) or not set(desired)<=set(KEYS):
            raise ValueError('Некорректный состав копии TCP-профиля')
        if any(not base.valid_value(k,v) for d in (before,desired) for k,v in d.items()):raise ValueError('Некорректные значения копии')
        if data.get('phase') not in ('applying','applied','rollback_failed') or data.get('managed_content')!=base.content(desired):
            raise ValueError('Некорректное состояние копии')
        old=data.get('file_before')
        if old is not None and (not isinstance(old,dict) or not isinstance(old.get('content'),str) or type(old.get('mode')) is not int or not 0<=old['mode']<=0o777):
            raise ValueError('Некорректная копия файла')
        return data

    def apply_selected(self,selected):
        with self.lock():
            plan=self.plan(selected=selected)
            if not plan['ready']:raise ValueError('; '.join(plan['blockers']))
            desired=plan['desired']
            snapshot={'phase':'applying','desired':desired,'before':{k:plan['current'][k] for k in desired},
                      'managed_content':base.content(desired),'file_before':self.file_snapshot(base.CONFIG)}
            self.save_snapshot(snapshot)
            try:
                base.atomic_write(self.path(base.CONFIG),snapshot['managed_content'],0o644)
                self.set_values(desired)
                snapshot['phase']='applied';self.save_snapshot(snapshot)
            except Exception:
                self.restore(snapshot)
                raise RuntimeError('Применение не удалось; прежние настройки восстановлены')
            return plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan','apply','rollback'))
    p.add_argument('--notsent-lowat',action='store_true');p.add_argument('--keep-cwnd',action='store_true');p.add_argument('--fastopen',action='store_true')
    a=p.parse_args()
    if a.action!='plan' and os.geteuid()!=0:p.error('Требуются права root')
    selected=[k for k,flag in zip(KEYS,(a.notsent_lowat,a.keep_cwnd,a.fastopen)) if flag]
    profile=Profile()
    if a.action=='rollback':
        profile.rollback();print('Предыдущие значения TCP восстановлены.');return
    result=profile.apply_selected(selected) if a.action=='apply' else profile.plan(selected=selected)
    for k,v in result['current'].items():print(k+': '+v+' → '+result['desired'].get(k,'без изменения'))
    for error in result['blockers']:print('  '+error)
    if a.action=='apply':print('TCP-профиль применён; копия: '+base.STATE)
    return 0 if result['ready'] else 1


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,RuntimeError) as error:sys.exit('Ошибка TCP-профиля: '+str(error))
