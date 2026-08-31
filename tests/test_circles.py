# -*- coding: utf-8 -*-
u"""Собранные кружки: телеграм принимает не всякое видео.

Требования жёсткие и молчаливые: неквадратное или длиннее минуты видео
уедет обычным сообщением или не уйдёт вовсе. Проверять это на живых людях
дорого, поэтому файлы меряются здесь.
"""
import hashlib
import io
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import config                                                # noqa: E402

CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'tools', 'circles.json')

MAX_SECONDS = 60
MAX_MB = 50                       # предел телеграма на отправку файлом


def circle_files():
    if not os.path.isdir(config.CIRCLES_DIR):
        return []
    return sorted(f for f in os.listdir(config.CIRCLES_DIR) if f.endswith('.mp4'))


def probe(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,codec_name',
        '-show_entries', 'format=duration', '-of', 'json', path])
    data = json.loads(raw.decode('utf-8'))
    stream = data['streams'][0]
    return stream, float(data['format']['duration'])


@pytest.mark.parametrize('name', circle_files())
def test_кружок_годится_для_телеграма(name):
    path = os.path.join(config.CIRCLES_DIR, name)
    stream, duration = probe(path)

    assert stream['width'] == stream['height'], u'%s не квадратный' % name
    assert stream['codec_name'] == 'h264', u'%s не h264: %s' % (name, stream['codec_name'])
    assert duration <= MAX_SECONDS, u'%s идёт %.1f сек' % (name, duration)
    assert os.path.getsize(path) < MAX_MB * 1048576, u'%s тяжелее %d МБ' % (name, MAX_MB)


def test_кружки_вообще_собраны():
    assert len(circle_files()) >= 12, u'соберите их: python tools/circles.py'


def test_отпечатки_исходников_совпадают():
    u"""Кружок должен быть собран из того файла, который записан в карте.

    Заказчик присылал один и тот же кружок под разными именами, а под старым
    именем — другое содержимое. Если исходники на месте, проверяем, что
    пересборка возьмёт именно те файлы, из которых собрано сейчас.
    """
    with io.open(CONF, encoding='utf-8') as handle:
        conf = json.load(handle)

    for item in conf['circles']:
        path = os.path.join(conf['src_dir'], item['src'] + '.MOV')
        if not item.get('sha1') or not os.path.exists(path):
            pytest.skip(u'исходников нет на этой машине')

        digest = hashlib.sha1()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
        assert digest.hexdigest() == item['sha1'], (
            u'%s собран не из того файла: %s' % (item['name'], item['src']))
