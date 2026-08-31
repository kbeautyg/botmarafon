# -*- coding: utf-8 -*-
u"""Разбор присланной пачки видео: что новое, что уже есть, чего не хватает.

Заказчик шлёт кружки пачками, и имена при пересылке съезжают: один и тот же
файл приходил и как IMG_0696, и как IMG_06936, а под именем IMG_0702 во
второй раз приехало содержимое IMG_0701. Разбирать такое по именам нельзя —
можно поставить в воронку не тот кружок и узнать об этом от людей.

Поэтому сверяем по отпечатку содержимого. Имя ни на что не влияет.

Запуск:  python tools/inbox.py "C:/Users/Sharp/Downloads"
         python tools/inbox.py IMG_0683.MOV IMG_06936.MOV ...

Папку удобно скармливать целиком, но если в ней лежит и постороннее — можно
перечислить именно присланные файлы.
"""
from __future__ import print_function

import hashlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, 'tools', 'circles.json')
CHUNK = 1 << 20


def sha1(path):
    digest = hashlib.sha1()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def known_sources(conf):
    u"""Отпечаток -> (имя исходника, какие кружки из него сделаны)."""
    table = {}
    for item in conf['circles']:
        mark = item.get('sha1')
        if not mark:
            continue
        entry = table.setdefault(mark, {'src': item['src'], 'circles': []})
        entry['circles'].append(item['name'])
    return table


def collect_paths(targets):
    u"""Разворачиваем аргументы: папка — это все видео внутри неё."""
    paths = []
    for target in targets:
        if os.path.isdir(target):
            paths.extend(os.path.join(target, name)
                         for name in sorted(os.listdir(target))
                         if name.lower().endswith(('.mov', '.mp4')))
        elif os.path.exists(target):
            paths.append(target)
        else:
            print(u'нет такого файла: %s' % target)
    return paths


def scan(targets):
    u"""Файлы с их отпечатками."""
    return [(os.path.basename(path), sha1(path), os.path.getsize(path))
            for path in collect_paths(targets)]


def report(conf, targets):
    known = known_sources(conf)
    files = scan(targets)
    if not files:
        return [u'Видео не нашлось: %s' % u', '.join(targets)]

    lines = [u'Разобрано файлов: %d' % len(files), u'']
    seen = {}
    fresh = []

    for name, mark, size in files:
        double = seen.get(mark)
        seen.setdefault(mark, name)
        match = known.get(mark)

        if double:
            lines.append(u'  %-14s — то же самое, что %s (дубль под другим именем)'
                         % (name, double))
        elif match:
            lines.append(u'  %-14s — уже есть: %s → %s'
                         % (name, match['src'], u', '.join(match['circles'])))
        else:
            lines.append(u'  %-14s — НОВОЕ, %.0f МБ' % (name, size / 1048576.0))
            fresh.append((name, mark))

    lines.append(u'')
    lines.append(u'Уникальных файлов в пачке: %d' % len(seen))

    # Чего в пачке не оказалось. Важно, когда заказчик говорит «вот все» —
    # часть кружков при этом может остаться в переписке.
    missing = [entry for mark, entry in known.items() if mark not in seen]
    if missing:
        lines.append(u'')
        lines.append(u'Нет в пачке (лежит у нас с прошлого раза):')
        for entry in sorted(missing, key=lambda e: e['src']):
            lines.append(u'  %s → %s' % (entry['src'], u', '.join(entry['circles'])))

    for name, why in conf.get('missing', {}).items():
        lines.append(u'')
        lines.append(u'Так и не прислан кружок %s: %s' % (name, why))

    if fresh:
        lines.append(u'')
        lines.append(u'Новое надо разметить: добавить в tools/circles.json '
                     u'и прогнать tools/transcribe.py, потом tools/circles.py')
    return lines


def main():
    if len(sys.argv) < 2:
        raise SystemExit(u'Укажите папку или файлы: python tools/inbox.py <что разбирать>')

    with io.open(CONF, encoding='utf-8') as handle:
        conf = json.load(handle)

    for line in report(conf, sys.argv[1:]):
        print(line)


if __name__ == '__main__':
    main()
