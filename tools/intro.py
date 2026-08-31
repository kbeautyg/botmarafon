# -*- coding: utf-8 -*-
u"""Заставка в начало записи каждого дня.

Заказчик: «в начале каждого дня сделать вон ту превьюшку» — и прислал её:
анимированный постер марафона «ПЕРЕЗАГРУЗКА на 4 сферы жизни». Он и идёт
в дело, media/intro/poster.mp4.

Постер вертикальный, 1080x1920, а записи горизонтальные. Вписать его
целиком нельзя: в полосу 16:9 он ужмётся до ленточки, где не прочитать ни
слова. Поэтому берём во всю ширину полосу с главным — заголовком, строкой
про четыре дня, подписью Павла и верхом мандалы, — а низ, где даты и
призыв писать в телеграм, отсекаем: марафон уже прошёл, в записи это лишнее.

Поверх кладём, какой это день. Дальше склейка БЕЗ перекодирования записи:
заставка кодируется ровно под её параметры, concat переписывает контейнер.

Запуск:  python tools/intro.py
"""
from __future__ import print_function

import io
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTER = os.path.join(ROOT, 'media', 'intro', 'poster.mp4')
WORK = os.path.join(ROOT, 'media', 'intro')

SOURCE_DIR = u'C:/Users/Sharp/Downloads/Telegram Desktop'
OUT_DIR = u'C:/Users/Sharp/Desktop/Марафон записи'

# Шрифт подписи. Системный: у него есть кириллица и он одинаково стоит на
# любой машине с Windows, в отличие от веб-шрифтов сайта.
FONT = 'C:/Windows/Fonts/segoeuib.ttf'

# Откуда брать полосу и какой высоты. 470 — под заголовком остаётся подпись
# Павла и верх мандалы с иконками сфер; выше уезжает в пустое небо, ниже
# срезается сам заголовок.
BAND_TOP = 470

# Название дня и цвет сферы — те же, что на сайте энергетического спортзала.
DAYS = {
    1: (u'Марафон - День 1 - Предназначение', u'ПРЕДНАЗНАЧЕНИЕ', 'f2ac4e'),
    2: (u'Марафон - День 2 - Отношения', u'ОТНОШЕНИЯ', 'd167e0'),
    3: (u'Мафарон - День 3 - Здоровье', u'ЗДОРОВЬЕ', '48dfa7'),   # опечатка у заказчика
    4: (u'Марафон - День 4 - Финансы', u'ФИНАНСЫ', '6a8cff'),
}


def probe(path):
    u"""Параметры записи: под них подгоняем заставку."""
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries',
        'stream=index,codec_type,width,height,r_frame_rate,sample_rate,'
        'channels,profile', '-show_entries', 'format=duration',
        '-of', 'json', path])
    data = json.loads(raw.decode('utf-8'))
    streams = data['streams']
    video = next(s for s in streams if s['codec_type'] == 'video')
    audio = next(s for s in streams if s['codec_type'] == 'audio')
    return {
        'width': video['width'], 'height': video['height'],
        'fps': video['r_frame_rate'],
        'profile': (video.get('profile') or 'high').lower(),
        'rate': str(audio.get('sample_rate', '48000')),
        'channels': audio.get('channels', 2),
        'video_index': video['index'], 'audio_index': audio['index'],
        'duration': float(data['format']['duration']),
    }


def _stage(day):
    u"""Временная папка со шрифтом и подписями рядом с ffmpeg.

    drawtext не переваривает windows-путь: двоеточие после буквы диска он
    считает концом значения, и как его ни экранируй, ffmpeg 8 ругается
    «No option name near». Поэтому кладём шрифт и тексты в одну временную
    папку, запускаем ffmpeg прямо в ней и пишем в фильтре голые имена
    файлов — двоеточий не остаётся вовсе. Кириллицу тоже отдаём файлом:
    в командной строке она бьётся.
    """
    folder = tempfile.mkdtemp(prefix='intro%d-' % day)
    shutil.copy(FONT, os.path.join(folder, 'font.ttf'))

    _, name, _ = DAYS[day]
    for filename, text in (('day.txt', u'ДЕНЬ %d' % day), ('name.txt', name)):
        with io.open(os.path.join(folder, filename), 'w', encoding='utf-8') as handle:
            handle.write(text)
    return folder


def build_clip(day, spec, dst):
    u"""Заставка под параметры записи.

    ПОРЯДОК ПОТОКОВ ОБЯЗАН СОВПАСТЬ с записью. concat сопоставляет дорожки
    по номеру, а не по типу: в записях зума первым идёт звук. Соберёшь
    заставку наоборот — склейка возьмёт картинку заставки со звуком записи,
    и двухчасовой день превратится в восьмичасовую кашу. Проверено.
    """
    tint = DAYS[day][2]
    poster = probe(POSTER)
    band = int(poster['width'] * spec['height'] / float(spec['width']))
    top = max(0, min(poster['height'] - band, BAND_TOP))
    height = spec['height']

    chain = (
        'crop=%d:%d:0:%d,scale=%d:%d,fps=%s,format=yuv420p,'
        # Подложка под подписью: без неё белый текст тонет в искрах мандалы.
        'drawbox=x=0:y=%d:w=iw:h=%d:color=black@0.5:t=fill,'
        'drawtext=fontfile=font.ttf:textfile=day.txt:fontcolor=0x%s:fontsize=%d:'
        'x=(w-text_w)/2:y=%d,'
        'drawtext=fontfile=font.ttf:textfile=name.txt:fontcolor=white:fontsize=%d:'
        'x=(w-text_w)/2:y=%d'
        % (poster['width'], band, top, spec['width'], height, spec['fps'],
           int(height * 0.74), int(height * 0.26),
           tint, int(height * 0.062), int(height * 0.775),
           int(height * 0.095), int(height * 0.855))
    )

    audio_first = spec['audio_index'] < spec['video_index']
    maps = ['-map', '0:a:0', '-map', '0:v:0'] if audio_first else \
           ['-map', '0:v:0', '-map', '0:a:0']

    stage = _stage(day)
    try:
        subprocess.check_call([
            'ffmpeg', '-v', 'error', '-i', POSTER,
            '-vf', chain,
            '-af', 'volume=-6dB,afade=t=out:st=%.1f:d=1,aformat=sample_rates=%s:'
                   'channel_layouts=%s' % (poster['duration'] - 1.0, spec['rate'],
                                           'stereo' if spec['channels'] == 2 else 'mono')] +
            maps + [
            '-c:v', 'libx264', '-profile:v', spec['profile'], '-preset', 'medium',
            '-crf', '20', '-r', spec['fps'], '-video_track_timescale', '90000',
            '-c:a', 'aac', '-b:a', '128k', '-ar', spec['rate'],
            '-movflags', '+faststart', dst, '-y'], cwd=stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def glue(clip, source, dst):
    u"""Заставка плюс запись, без перекодирования записи."""
    listing = dst + '.txt'
    with io.open(listing, 'w', encoding='utf-8') as handle:
        for part in (clip, source):
            handle.write(u"file '%s'\n" % part.replace('\\', '/').replace("'", r"'\''"))
    try:
        # Только звук и картинка: в записях есть ещё служебный data-поток,
        # которого у заставки нет, — на нём склейка спотыкается.
        subprocess.check_call([
            'ffmpeg', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', listing,
            '-map', '0:a:0', '-map', '0:v:0', '-c', 'copy',
            '-movflags', '+faststart', dst, '-y'])
    finally:
        os.remove(listing)


def main():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    for day in sorted(DAYS):
        source = os.path.join(SOURCE_DIR, DAYS[day][0] + '.mp4')
        if not os.path.exists(source):
            print(u'нет записи дня %d: %s' % (day, source))
            continue

        spec = probe(source)
        clip = os.path.join(WORK, '_clip%d.mp4' % day)
        build_clip(day, spec, clip)

        dst = os.path.join(OUT_DIR, u'День %d.mp4' % day)
        glue(clip, source, dst)

        was, now = spec['duration'], probe(dst)['duration']
        grew = now - was
        print(u'День %d: %.0f мин → %.0f мин (+%.1f сек заставки), %.0f МБ  %s'
              % (day, was / 60, now / 60, grew,
                 os.path.getsize(dst) / 1048576.0,
                 u'✓' if abs(grew - probe(clip)['duration']) < 1.0 else u'✗ ДЛИНА УЕХАЛА'))
        os.remove(clip)

    print(u'\nготовые записи: %s' % OUT_DIR)


if __name__ == '__main__':
    main()
