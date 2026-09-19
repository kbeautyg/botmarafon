# -*- coding: utf-8 -*-
u"""Голосовые и кружки, записанные прямо в пульте.

AleX 19.09.2026: «здесь ещё необходимо, чтобы можно было голосовухи
отправлять и видео кружочки нашим контактам здесь в переписке».

Браузер записывает в своём формате — чаще всего webm с opus внутри, на
айфоне mp4. Telegram же принимает голосовое только как ogg/opus, а кружок
— только как квадратный mp4 не длиннее минуты. Поэтому запись
перекодируется на сервере (ffmpeg), и лишь потом уходит человеку.

Если ffmpeg в контейнере не оказалось или перекодировка не удалась, запись
всё равно уходит — обычным аудио или видео. Это хуже вида, но лучше
молчания: сообщение доходит до человека, а команда видит, что ушло.
"""
import asyncio
import logging
import os
import shutil
import tempfile

log = logging.getLogger(__name__)

# Предел записи: минута для кружка — предел самого Telegram, голосовое
# держим в тех же рамках, чтобы не отправлять людям десятиминутные письма.
MAX_SECONDS = 60
# Сколько ждём ffmpeg: перекодировать минуту звука или квадратное видео он
# успевает за секунды, а застрявший процесс держал бы бота.
TIMEOUT = 90
# Сторона кружка у Telegram — 384 или 640 точек; берём среднее и не больше.
NOTE_SIDE = 384


def have_ffmpeg() -> bool:
    return bool(shutil.which('ffmpeg'))


async def _run(args: list) -> bool:
    u"""Запустить ffmpeg. True — получилось."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except OSError as err:
        log.warning(u'ffmpeg не запустился: %s', err)
        return False
    try:
        _, problem = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        log.warning(u'ffmpeg не уложился в %d с', TIMEOUT)
        return False
    if process.returncode:
        log.warning(u'ffmpeg вернул %s: %s', process.returncode,
                    (problem or b'')[-300:].decode('utf-8', 'replace'))
        return False
    return True


async def to_voice(source: str) -> str | None:
    u"""Запись → голосовое (ogg/opus). None — перекодировать не вышло."""
    target = source + '.ogg'
    ok = await _run(['ffmpeg', '-v', 'error', '-y', '-i', source,
                     '-vn', '-ac', '1', '-c:a', 'libopus', '-b:a', '32k',
                     '-t', str(MAX_SECONDS), target])
    return target if ok and os.path.getsize(target) else None


async def to_note(source: str) -> str | None:
    u"""Запись → кружок (квадратный mp4). None — перекодировать не вышло.

    Кадр обрезаем по короткой стороне и ужимаем до стороны кружка: иначе
    Telegram покажет видео прямоугольником, а не видеосообщением.
    """
    target = source + '.mp4'
    square = ('crop=min(iw\\,ih):min(iw\\,ih),scale=%d:%d' % (NOTE_SIDE, NOTE_SIDE))
    ok = await _run(['ffmpeg', '-v', 'error', '-y', '-i', source,
                     '-t', str(MAX_SECONDS), '-vf', square,
                     '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                     '-c:a', 'aac', '-b:a', '64k', '-movflags', '+faststart', target])
    return target if ok and os.path.getsize(target) else None


def keep(data: bytes, suffix: str = '.bin') -> str:
    u"""Сохранить присланную запись во временный файл и вернуть путь."""
    handle, path = tempfile.mkstemp(suffix=suffix, prefix='panel-')
    with os.fdopen(handle, 'wb') as file:
        file.write(data)
    return path


def forget(*paths) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            continue
