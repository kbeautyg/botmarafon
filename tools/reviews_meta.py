# -*- coding: utf-8 -*-
u"""Размеры и длительность видеоотзывов — в media/reviews/meta.json.

Bot API без width/height/duration показывает видео квадратной заглушкой
до первого нажатия: заказчик 05.09.2026 — «этот отзыв в ужасном
качестве, какой-то кривой». На сервере бота ffprobe нет, поэтому размеры
снимаются здесь, при сборке, и едут в репозиторий рядом с файлами.

Запуск:  python tools/reviews_meta.py
"""
from __future__ import print_function

import io
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEWS = os.path.join(ROOT, 'media', 'reviews')


def probe(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:format=duration',
        '-of', 'json', path])
    data = json.loads(raw.decode('utf-8'))
    stream = data['streams'][0]
    return {'width': int(stream['width']), 'height': int(stream['height']),
            'duration': int(round(float(data['format']['duration'])))}


def main():
    meta = {}
    for name in sorted(os.listdir(REVIEWS)):
        if name.endswith('.mp4'):
            meta[name] = probe(os.path.join(REVIEWS, name))
            print(name, meta[name])
    with io.open(os.path.join(REVIEWS, 'meta.json'), 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(meta, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
