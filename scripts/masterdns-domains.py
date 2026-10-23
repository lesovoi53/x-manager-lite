#!/usr/bin/env python3
"""Edit one MasterDnsVPN DOMAIN array without changing credentials or lifecycle intent."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

CONFIG = Path('/etc/masterdns/server_config.toml')
UNIT = 'masterdns.service'
METHODS = ('NONE (без шифрования)', 'XOR', 'ChaCha20', 'AES-128-GCM', 'AES-192-GCM', 'AES-256-GCM')


def validate_method(method):
    if type(method) is not int or not 0 <= method < len(METHODS):
        raise ValueError('Метод шифрования должен быть целым числом от 0 до 5.')
    return method


def read_method(text):
    # ServerConfig.DefaultServerConfig uses XOR when the property is absent.
    return validate_method(tomllib.loads(text).get('DATA_ENCRYPTION_METHOD', 1))


def key_length(method):
    return {3: 16, 4: 24}.get(validate_method(method), 32)


def key_path(path, text):
    value = tomllib.loads(text).get('ENCRYPTION_KEY_FILE', '') or 'encrypt_key.txt'
    if not isinstance(value, str):
        raise ValueError('Некорректный путь к ключу MasterDNS.')
    result = Path(value)
    result = result if result.is_absolute() else path.parent / result
    if result.resolve() == path.resolve():
        raise ValueError('Файлы ключа и конфигурации должны различаться.')
    return result


def check_key(data, method):
    # Go checks UTF-8 byte length after strings.TrimSpace, not hex-decoded length.
    try:
        size = len(data.decode('utf-8').strip().encode('utf-8'))
    except UnicodeDecodeError:
        raise ValueError('Ключ должен содержать текст UTF-8.') from None
    if size != key_length(method):
        raise ValueError(f'{METHODS[method]} требует ключ длиной {key_length(method)} байт текста. Ключ не изменён.')


def replace_method(text, method):
    validate_method(method)
    before = tomllib.loads(text)
    read_method(text)
    if 'DATA_ENCRYPTION_METHOD' in before:
        pattern = r'(?m)^([ \t]*DATA_ENCRYPTION_METHOD[ \t]*=[ \t]*)([+]?\d+)(?=[ \t]*(?:#|\r?$))'
        updated, count = re.subn(pattern, lambda m: m[1] + str(method), text)
        if count != 1:
            raise ValueError('Не удалось безопасно найти DATA_ENCRYPTION_METHOD.')
    else:
        updated = f'DATA_ENCRYPTION_METHOD = {method}\n' + text
    after = tomllib.loads(updated)
    before.pop('DATA_ENCRYPTION_METHOD', None)
    after.pop('DATA_ENCRYPTION_METHOD', None)
    if before != after or read_method(updated) != method:
        raise ValueError('Отказ изменять другие поля конфигурации.')
    return updated


def validate(domains):
    if not isinstance(domains, list) or not 1 <= len(domains) <= 16:
        raise ValueError('Нужно от 1 до 16 доменов.')
    seen = set()
    for domain in domains:
        if (not isinstance(domain, str) or len(domain) > 253 or '.' not in domain
                or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', p)
                           for p in domain.split('.'))):
            raise ValueError('Некорректный домен: используйте DNS-имя без пробелов и завершающей точки.')
        if domain.lower() in seen:
            raise ValueError('Этот домен уже есть в списке (регистр не учитывается).')
        seen.add(domain.lower())
    return list(domains)


def read_domains(text):
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ValueError('Некорректный TOML MasterDNS; файл не изменён.') from None
    return validate(config.get('DOMAIN'))


def replace_domains(text, domains):
    """Locate the top-level array lexically; preserve all bytes outside its value."""
    read_domains(text)
    values = validate(domains)
    # MasterDNS uses a flat TOML document. A later table DOMAIN is not this key.
    match = re.search(r'(?m)^[ \t]*DOMAIN[ \t]*=[ \t]*', text)
    if not match or text[match.end():match.end()+1] != '[':
        raise ValueError('Не найден массив DOMAIN в конфигурации.')
    start = match.end()
    i, depth, quote, comment = start, 0, '', False
    while i < len(text):
        c = text[i]
        if comment:
            comment = c != '\n'
        elif quote:
            if quote.startswith('"') and c == '\\':
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = ''
                continue
        elif c == '#':
            comment = True
        elif c in ('"', "'"):
            quote = c * 3 if text.startswith(c * 3, i) else c
            i += len(quote)
            continue
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                result = text[:start] + json.dumps(values) + text[i+1:]
                before = tomllib.loads(text)
                after = tomllib.loads(result)
                before.pop('DOMAIN')
                after.pop('DOMAIN')
                if read_domains(result) != values or before != after:
                    raise ValueError('Не удалось безопасно обновить DOMAIN.')
                return result
        i += 1
    raise ValueError('Незавершённый массив DOMAIN.')


def snapshot(path):
    if path.is_symlink():
        raise ValueError('Конфигурация MasterDNS не должна быть symlink.')
    return path.read_bytes()


def atomic_write(path, data, metadata=None):
    if path.is_symlink():
        raise ValueError('Конфигурация MasterDNS не должна быть symlink.')
    fd, name = tempfile.mkstemp(prefix='.masterdns-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, metadata.st_mode & 0o7777 if metadata else 0o600)
        if metadata and os.name == 'posix':
            os.chown(name, metadata.st_uid, metadata.st_gid)
        os.replace(name, path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def controller():
    spec = importlib.util.spec_from_file_location('masterdns_control', Path(__file__).with_name('service-control.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def bounded(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Ошибка systemctl; проверьте журнал службы локально.')
        return result.stdout.strip()
    return module.Controller(run=bounded)


def apply_change(path, expected, transform, control=None, sleep=time.sleep, prepare=None):
    control = control or controller()
    # One lifecycle lock: never call Controller.change(), which locks again.
    with control.lock():
        original = snapshot(path)
        if original != expected:
            raise ValueError('Конфигурация изменилась извне. Откройте меню заново.')
        updated = transform(original.decode('utf-8')).encode('utf-8')
        extra = prepare(original.decode('utf-8')) if prepare else []
        if updated == original and not extra:
            return False
        metadata = path.stat()
        state = control.show(UNIT)
        if state.get('LoadState') not in ('loaded', 'masked'):
            raise ValueError('Служба MasterDNS не установлена.')
        if state.get('ActiveState') not in ('active', 'inactive', 'failed'):
            raise ValueError('Дождитесь завершения перехода состояния MasterDNS.')
        active = state.get('ActiveState') == 'active'
        if active:
            control.check_owned(UNIT)
            if not control.allowed(UNIT) or state.get('LoadState') == 'masked' or state.get('UnitFileState', '').startswith('masked'):
                raise ValueError('Перезапуск запрещён текущей политикой службы; настройки сохранены не будут.')
        def restart():
            control.ctl('restart', UNIT)
            for _ in range(2):
                sleep(2)
                if control.show(UNIT).get('ActiveState') != 'active':
                    raise RuntimeError('MasterDNS не остался активным после перезапуска.')
        targets = extra + [(path, original, updated, metadata)]
        try:
            # Config and key are separate files. Stop the active process before
            # changing key length so an automatic restart cannot regenerate a
            # key from the temporary old-method/new-key combination.
            if active and extra:
                control.ctl('stop', UNIT)
                if control.show(UNIT).get('ActiveState') not in ('inactive', 'failed'):
                    raise RuntimeError('Не удалось остановить MasterDNS перед сменой ключа.')
            for target, previous, desired, details in targets:
                atomic_write(target, desired, details)
            if active:
                restart()
        except BaseException as error:
            try:
                failures = []
                for target, previous, desired, details in targets:
                    try:
                        atomic_write(target, previous, details)
                    except Exception:
                        failures.append(target)
                if failures:
                    raise RuntimeError('Не удалось восстановить файлы.')
                if active:
                    restart()
            except Exception:
                raise RuntimeError('Ошибка применения и восстановления. Проверьте конфигурацию и журнал MasterDNS локально.') from error
            raise RuntimeError('Изменения отменены: прежняя конфигурация и состояние службы восстановлены.') from error
        return active


def save(path, domains, expected, control=None, sleep=time.sleep):
    def transform(text):
        return text if validate(domains) == read_domains(text) else replace_domains(text, domains)
    return apply_change(path, expected, transform, control, sleep)


def save_method(path, method, expected, control=None, sleep=time.sleep, regenerate=False, expected_key=None):
    validate_method(method)
    def prepare(text):
        target = key_path(path, text)
        previous = snapshot(target)
        if expected_key is not None and previous != expected_key:
            raise ValueError('Ключ изменился извне. Откройте меню заново.')
        check_key(previous, read_method(text))
        if regenerate:
            desired = secrets.token_hex(key_length(method) // 2).encode('ascii')
            check_key(desired, method)
            return [(target, previous, desired, target.stat())]
        check_key(previous, method)
        return []
    def transform(text):
        return text if method == read_method(text) else replace_method(text, method)
    return apply_change(path, expected, transform, control, sleep, prepare)


def save_key(path, desired, expected, control=None, sleep=time.sleep):
    def prepare(text):
        method = read_method(text)
        target = key_path(path, text)
        previous = snapshot(target)
        check_key(previous, method)
        check_key(desired, method)
        return [] if previous == desired else [(target, previous, desired, target.stat())]
    return apply_change(path, expected, lambda text: text, control, sleep, prepare)


def method_menu(path=CONFIG, control=None, ask=input, output=print):
    original = snapshot(path)
    text = original.decode('utf-8')
    current = read_method(text)
    old_key = snapshot(key_path(path, text))
    output(f'Текущий метод: {current} — {METHODS[current]}')
    for method, name in enumerate(METHODS):
        output(f'[{method}] {name}')
    output('[Enter] Назад без изменений')
    output('Один метод для всей службы. После смены клиентам нужен новый экспорт профиля; выданные подписки автоматически не обновляются.')
    try:
        choice = ask('Новый метод [0–5]: ').strip()
        if not choice:
            return
        if choice not in tuple(str(n) for n in range(6)):
            raise ValueError('Выберите метод 0–5 или Enter для отмены.')
        method = int(choice)
        if method == current:
            output('Метод уже выбран; изменений нет.')
            return
        check_key(old_key, current)
        regenerate = False
        try:
            check_key(old_key, method)
        except ValueError:
            output(f'Метод требует {key_length(method)} байт текста ключа. Прежний ключ несовместим.')
            regenerate = ask('Создать новый ключ и использовать его в новых экспортируемых ссылках? [y/N]: ').strip().lower() == 'y'
            if not regenerate:
                output('Отменено. Метод и ключ не изменены.')
                return
        if ask(f'Применить {METHODS[method]} и перезапустить работающую службу? [y/N]: ').strip().lower() != 'y':
            output('Отменено. Метод и ключ не изменены.')
            return
        active = save_method(path, method, original, control, regenerate=regenerate, expected_key=old_key)
        output('Метод сохранён; служба перезапущена.' if active else 'Метод сохранён; остановленная служба не запускалась.')
        output('Выдайте клиентам новый экспорт. База подписок не изменялась.')
    except (EOFError, KeyboardInterrupt):
        output('\nВыход без сохранения.')
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        output(str(error))


def dns_check(domains, run=subprocess.run, clock=time.monotonic):
    validate(domains)
    print('Информационная проверка делегирования; сохранение от результата не зависит.')
    print('NS запрашивается у родительской авторитетной зоны. Пустой ответ самого туннеля не означает ошибку делегирования.')
    if not shutil.which('dig'):
        print('dig отсутствует. Ничего не устанавливаем; проверьте NS у DNS-провайдера.')
        return
    deadline = clock() + 45
    def query(*args):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError
        result = run(['dig', '+time=2', '+tries=1', *args], capture_output=True, text=True, timeout=min(3, remaining))
        return result.stdout if result.returncode == 0 else ''
    try:
        for domain in domains:
            print('\n' + domain + ':')
            labels = domain.split('.')
            authorities = []
            for offset in range(1, len(labels)):
                parent = '.'.join(labels[offset:])
                answer = query('+short', parent, 'NS')
                authorities = [line.strip().rstrip('.') for line in answer.splitlines()
                               if re.fullmatch(r'[A-Za-z0-9.-]+\.', line.strip())]
                if authorities:
                    break
            if not authorities:
                print('  Родительская зона не определена; результат неопределён.')
                continue
            answer = query('@' + authorities[0], '+norecurse', '+noall', '+answer', '+authority', domain, 'NS')
            records = [line for line in answer.splitlines()
                       if re.match(re.escape(domain) + r'\.\s+\d+\s+IN\s+NS\s+', line, re.I)]
            print('  Родитель: ' + parent + '; сервер: ' + authorities[0])
            if records:
                for record in records:
                    print('  ' + record)
                print('  Делегирование видно. Адреса NS и реальное подключение клиента проверяются отдельно.')
            else:
                print('  NS в ответе родителя не обнаружен; проверьте панель DNS/сеть. Это не проверка доступности туннеля.')
    except (subprocess.TimeoutExpired, TimeoutError):
        print('Лимит времени проверки исчерпан; оставшиеся результаты неопределённы.')


def menu(path=CONFIG, control=None, ask=input, output=print):
    original = snapshot(path)
    domains = read_domains(original.decode('utf-8'))
    while True:
        output('\nДомены этого сервера')
        for number, domain in enumerate(domains, 1):
            output(f'{number}. {domain}' + (' — первый для совместимости' if number == 1 else ''))
        output('[1] Добавить домен\n[2] Удалить домен\n[3] Сделать первым\n[4] Проверить настройки DNS\n[5] Сохранить и перезапустить MasterDnsVPN\n[0] Назад без сохранения')
        output('Изменения применяются после сохранения. Ключ, маршрут и выданные подписки сохраняются.')
        try:
            choice = ask('Действие: ').strip()
            if choice == '0':
                return
            if choice == '1':
                candidate = ask('Новый домен (без автоматического префикса): ').strip()
                domains = validate(domains + [candidate])
            elif choice in ('2', '3'):
                number = ask('Номер домена: ').strip()
                if not number.isdecimal() or not 1 <= int(number) <= len(domains):
                    raise ValueError('Выберите номер домена из списка.')
                index = int(number) - 1
                if choice == '2':
                    domains = validate(domains[:index] + domains[index+1:])
                else:
                    domains = [domains[index]] + domains[:index] + domains[index+1:]
            elif choice == '4':
                dns_check(domains)
            elif choice == '5':
                active = save(path, domains, original, control)
                output('Домены сохранены; MasterDNS перезапущен.' if active else 'Домены сохранены; остановленная служба не запускалась (или изменений не было).')
                return
            else:
                output('Выберите номер из списка.')
        except (EOFError, KeyboardInterrupt):
            output('\nВыход без сохранения.')
            return
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            output(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('list', 'validate', 'menu', 'check', 'method', 'method-name', 'key-length', 'key-path', 'key', 'set-key', 'method-menu'))
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('domains', nargs='*')
    args = parser.parse_args()
    if args.action == 'validate':
        validate(args.domains)
    elif args.action == 'list':
        print('\n'.join(read_domains(snapshot(args.config).decode('utf-8'))))
    elif args.action == 'menu':
        menu(args.config)
    elif args.action == 'check':
        dns_check(args.domains or read_domains(snapshot(args.config).decode('utf-8')))
    elif args.action == 'method-menu':
        method_menu(args.config)
    elif args.action in ('method', 'method-name', 'key-length'):
        method = read_method(snapshot(args.config).decode('utf-8'))
        print(method if args.action == 'method' else METHODS[method] if args.action == 'method-name' else key_length(method))
    elif args.action in ('key-path', 'key'):
        text = snapshot(args.config).decode('utf-8')
        target = key_path(args.config, text)
        if args.action == 'key-path':
            print(target)
        else:
            data = snapshot(target)
            check_key(data, read_method(text))
            print(data.decode('utf-8').strip())
    elif args.action == 'set-key':
        desired = sys.stdin.buffer.read(4097)
        if len(desired) > 4096:
            raise ValueError('Некорректная длина ключа.')
        active = save_key(args.config, desired, snapshot(args.config))
        print('Ключ сохранён; служба перезапущена.' if active else 'Ключ сохранён; остановленная служба не запускалась (или изменений не было).')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        sys.exit('MasterDNS: ' + str(error))
