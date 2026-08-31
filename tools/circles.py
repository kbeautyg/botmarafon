# -*- coding: utf-8 -*-
u"""Сборка кружков из исходных .MOV.

Кружок в телеграме — не просто квадратное видео. Ограничения жёсткие:
квадрат, не длиннее 60 секунд, h264 + aac. Исходники — 4K-селфи с
айфона в вертикаль, по 200-500 МБ, с поворотом в метаданных.

Два места, где легко ошибиться:

1. КРОП. Кружок круглый: всё, что дальше радиуса от центра, зритель не
   увидит вообще. Квадрат по центру кадра сажает лицо в верхнюю кромку
   круга и срезает лоб. Поэтому центр лица берём из facecrop.py (медиана
   по пяти кадрам) и сажаем его на 46% высоты — с запасом на подбородок.

2. ДЛИНА. Приветственный кружок идёт 84 секунды. Телеграм столько не
   примет, а вырезать середину — потерять смысл: там метод и анонс
   первого дня. Режем на два кружка по концу фразы, они уходят подряд.

Запуск:  python tools/circles.py [имя ...]
"""
from __future__ import print_function

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, 'tools', 'circles.json')
OUT = os.path.join(ROOT, 'media', 'circles')

MAX_SECONDS = 60


def probe(path):
    u"""Размер кадра после поворота и длительность исходника."""
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:stream_side_data=rotation',
        '-show_entries', 'format=duration', '-of', 'json', path])
    data = json.loads(raw.decode('utf-8'))
    stream = data['streams'][0]
    width, height = stream['width'], stream['height']
    # Поворот лежит в одном из элементов side_data_list, а соседние приходят
    # пустыми — читать их подряд нельзя, пустой затрёт найденное значение.
    rotation = 0
    for side in stream.get('side_data_list', []):
        if 'rotation' in side:
            rotation = int(side['rotation'])
            break
    if abs(rotation) == 90:
        width, height = height, width
    return width, height, float(data['format']['duration'])


def build(conf, item):
    src = os.path.join(conf['src_dir'], item['src'] + '.MOV')
    if not os.path.exists(src):
        raise SystemExit(u'нет исходника: %s' % src)

    width, height, duration = probe(src)
    start, end = item.get('cut', [0, duration])
    length = end - start
    if length > MAX_SECONDS:
        raise SystemExit(u'%s: %.1f сек, телеграм примет не больше %d'
                         % (item['name'], length, MAX_SECONDS))

    # Квадрат во всю ширину кадра; двигать его можно только по вертикали.
    side = min(width, height)
    top = item['cy'] * height - conf['face_y'] * side
    top = max(0, min(height - side, int(round(top))))
    left = max(0, (width - side) // 2)

    size = conf['size']
    dst = os.path.join(OUT, item['name'] + '.mp4')
    cmd = [
        'ffmpeg', '-v', 'error', '-ss', '%.3f' % start, '-t', '%.3f' % length,
        '-i', src,
        '-vf', 'crop=%d:%d:%d:%d,scale=%d:%d,fps=30' % (side, side, left, top, size, size),
        '-c:v', 'libx264', '-profile:v', 'main', '-pix_fmt', 'yuv420p',
        '-b:v', conf['bitrate'], '-maxrate', conf['bitrate'], '-bufsize', '2800k',
        '-c:a', 'aac', '-b:a', '96k', '-ac', '1', '-ar', '44100',
        '-movflags', '+faststart', dst, '-y']
    subprocess.check_call(cmd)
    return dst, length, os.path.getsize(dst)


def main(argv):
    with open(CONF, encoding='utf-8') as handle:
        conf = json.load(handle)
    if not os.path.isdir(OUT):
        os.makedirs(OUT)

    wanted = set(argv) or None
    for item in conf['circles']:
        if wanted and item['name'] not in wanted:
            continue
        dst, length, size = build(conf, item)
        print(u'%-12s %5.1f сек  %5.1f МБ  <- %s'
              % (item['name'], length, size / 1048576.0, item['src']))

    for name, why in conf.get('missing', {}).items():
        print(u'НЕ ХВАТАЕТ %s: %s' % (name, why))


if __name__ == '__main__':
    main(sys.argv[1:])
