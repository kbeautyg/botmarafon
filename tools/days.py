# -*- coding: utf-8 -*-
u"""Сборка записей дней марафона: заставка дня + превью дня на сессии.

Заказчик (30.08 и 03.09.2026): в начале каждой записи — баннер дня со
звуком, а «когда Павел закрывает глаза и уходит в энергию» — вместо
пустого экрана картинка. Сначала это была схема тонкого плана, но Павел
05.09.2026, посмотрев первый день: «когда я делаю сессию и молчу, когда
меня не видно и выключена камера, надо чтобы была видна эта превьюшка,
которую вы делали с Лёхой» — то есть афиша дня с энергией, идущей в
сферу дня. Это последний кадр заставки; он же становится обложкой
видео в боте (media/intro/coverN.jpg): «первый день должен появляться
с превью, где энергия идёт в предназначение».

Что на самом деле в записях. Перед сессией Павел выключает камеру в
зуме, и вместо него на 20–25 минут остаётся серая заглушка «Метод
Finish» — это и есть тот «чёрный экран». Заглушку находим сами: кусок
кадра над головой Павла (стена с картиной) в живом видео пёстрый, а на
заглушке — ровный серый. Проход по записи с частотой 5 кадров в секунду
даёт границы с точностью 0,2 с. Берём только отрезки длиннее минуты:
короче — хвосты, где камера выключена уже после прощания.

Заставки — четыре ролика от заказчика, вертикальные 9:16, у каждого
свой день в заголовке. В горизонтальный кадр кладём афишу целиком, а по
бокам — её же размытый неподвижный кадр, без чёрных полей.

Записи в зуме 640×360, а схема и заставка полны мелкого текста — в 360p
их не прочитать. Поэтому собираем в 1280×720: запись растягивается (хуже
не станет, телефон всё равно растянет), схема и заставка остаются
чёткими. Всё кодируется заново одним проходом ffmpeg: заставка → запись
со схемой, звук единой дорожкой.

Запуск:  python tools/days.py [1 2 3 4]
"""
from __future__ import print_function

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(ROOT, 'media', 'intro')

SOURCE_DIR = u'C:/Users/Sharp/Downloads/Telegram Desktop'
OUT_DIR = u'C:/Users/Sharp/Desktop/Марафон записи'

# Имена файлов у заказчика; «Мафарон» — его опечатка, так и лежит.
DAYS = {
    1: u'Марафон - День 1 - Предназначение',
    2: u'Марафон - День 2 - Отношения',
    3: u'Мафарон - День 3 - Здоровье',
    4: u'Марафон - День 4 - Финансы',
}

W, H = 1280, 720
FPS = 25
SCAN_FPS = 5          # шаг поиска заглушки, кадров в секунду
MIN_SESSION = 60.0    # секунд: короче — не сессия, а хвост записи
FADE = 0.6            # секунд: появление и уход схемы

# Где смотреть на заглушку: полоска над головой Павла, внутри его
# вертикального кадра (запись — телефон посреди чёрных полей).
PROBE_CROP = 'crop=iw*0.1875:ih*0.11:iw*0.406:ih*0.028'


def duration(path):
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'csv=p=0', path])
    return float(raw.decode('ascii').strip())


def peak(path):
    u"""Пик громкости, дБ. Заставки от заказчика записаны тихо (у первых
    двух пик −20 дБ против −4 у речи Павла) — выравниваем каждую под −6."""
    out = subprocess.run(
        ['ffmpeg', '-v', 'info', '-i', path, '-vn', '-af', 'volumedetect',
         '-f', 'null', '-'], capture_output=True, text=True).stderr
    return float(re.search(r'max_volume: ([-0-9.]+) dB', out).group(1))


def scan(source, stage):
    u"""Отрезки, где вместо Павла заглушка зума: [(начало, конец), …]."""
    # metadata=print не берёт windows-путь с двоеточием — пишем в файл
    # с голым именем, запустив ffmpeg внутри временной папки.
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-i', source,
        '-vf', 'fps=%d,%s,signalstats,metadata=print:file=scan.txt'
               % (SCAN_FPS, PROBE_CROP),
        '-f', 'null', '-'], cwd=stage)

    frames = []
    stamp, seen = 0.0, {}
    with open(os.path.join(stage, 'scan.txt')) as handle:
        for line in handle:
            if line.startswith('frame:'):
                stamp = float(re.search(r'pts_time:([0-9.]+)', line).group(1))
                seen = {}
            elif line.startswith('lavfi.signalstats.'):
                key, _, value = line.strip().partition('=')
                seen[key.rsplit('.', 1)[1]] = float(value)
                if key.endswith('YMAX'):    # YMIN и YAVG уже прочитаны
                    is_flat = (seen['YMAX'] - seen['YMIN'] <= 8
                               and 12 < seen['YAVG'] < 70)
                    frames.append((stamp, is_flat))

    segments, start = [], None
    for stamp, is_flat in frames:
        if is_flat and start is None:
            start = stamp
        elif not is_flat and start is not None:
            segments.append((start, stamp))
            start = None
    if start is not None:
        segments.append((start, frames[-1][0] + 1.0 / SCAN_FPS))
    return [s for s in segments if s[1] - s[0] >= MIN_SESSION]


def build(day, source, segments, stage, dst):
    intro = os.path.join(MEDIA, 'day%d.mp4' % day)
    intro_len = duration(intro)
    gain = -6.0 - peak(intro)

    # Фон заставки — её же первый кадр, размытый; он не движется.
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-ss', '0.2', '-i', intro, '-frames:v', '1',
        os.path.join(stage, 'bg.png'), '-y'])
    # Превью дня — последний кадр заставки (сфера дня подсвечена) в том же
    # кадре 16:9 с размытыми боками. Идёт на сессию и обложкой в бота.
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-sseof', '-0.15', '-i', intro, '-frames:v', '1',
        '-update', '1', os.path.join(stage, 'last.png'), '-y'])
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-loop', '1', '-i', 'bg.png', '-i', 'last.png',
        '-filter_complex',
        '[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,'
        'boxblur=16:2,eq=saturation=1.15[bg];[1:v]scale=-2:%d[fg];'
        '[bg][fg]overlay=(W-w)/2:0:shortest=1' % (W, H, W, H, H),
        '-frames:v', '1', '-update', '1', 'cover.png', '-y'], cwd=stage)
    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-i', os.path.join(stage, 'cover.png'), '-q:v', '3',
        os.path.join(MEDIA, 'cover%d.jpg' % day), '-y'])

    enable = '+'.join('between(t,%.2f,%.2f)' % (a, b - 1.0 / FPS)
                      for a, b in segments)
    fades = ''.join(
        ',fade=t=in:st=%.2f:d=%.1f:alpha=1,fade=t=out:st=%.2f:d=%.1f:alpha=1'
        % (a, FADE, b - FADE, FADE) for a, b in segments)

    graph = (
        # заставка: афиша целиком по центру, по бокам размытый кадр
        '[1:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,'
        'boxblur=16:2,eq=saturation=1.15,setsar=1[bg];'
        '[0:v]scale=-2:%d,fps=%d,setsar=1[fg];'
        '[bg][fg]overlay=(W-w)/2:0:shortest=1,format=yuv420p,'
        'trim=duration=%.3f,fade=t=out:st=%.3f:d=0.4[iv];'
        '[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=%.1fdB,'
        'apad=whole_dur=%.3f,atrim=duration=%.3f,afade=t=out:st=%.3f:d=0.4[ia];'
        # запись: растянуть до 720p, поверх — схема, пока камера выключена
        '[2:v]setpts=PTS-STARTPTS,scale=%d:%d:flags=lanczos,fps=%d,setsar=1,'
        'format=yuv420p[rv0];'
        '[3:v]scale=%d:%d,format=rgba%s[sch];'
        "[rv0][sch]overlay=0:0:enable='%s':shortest=1,fade=t=in:st=0:d=0.5[rv];"
        '[2:a]aformat=sample_rates=48000:channel_layouts=stereo,'
        'asetpts=PTS-STARTPTS[ra];'
        '[iv][ia][rv][ra]concat=n=2:v=1:a=1[v][a]'
        % (W, H, W, H, H, FPS,
           intro_len, intro_len - 0.4,
           gain, intro_len, intro_len, intro_len - 0.4,
           W, H, FPS,
           W, H, fades,
           enable))

    subprocess.check_call([
        'ffmpeg', '-v', 'error', '-nostats',
        '-i', intro,
        '-loop', '1', '-framerate', str(FPS), '-i', 'bg.png',
        '-i', source,
        '-loop', '1', '-framerate', str(FPS), '-i', 'cover.png',
        '-filter_complex', graph, '-map', '[v]', '-map', '[a]',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '26',
        '-profile:v', 'high', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '160k',
        '-movflags', '+faststart', dst, '-y'], cwd=stage)
    return intro_len


def hms(seconds):
    seconds = int(round(seconds))
    return '%d:%02d:%02d' % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def main(argv):
    # Консоль Windows — cp1251, стрелки и галочки в отчёте в неё не лезут.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    days = [int(a) for a in argv] or sorted(DAYS)
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    for day in days:
        source = os.path.join(SOURCE_DIR, DAYS[day] + '.mp4')
        if not os.path.exists(source):
            print(u'нет записи дня %d: %s' % (day, source))
            continue

        stage = tempfile.mkdtemp(prefix='day%d-' % day)
        try:
            segments = scan(source, stage)
            print(u'День %d: схема на %s' % (day, u'; '.join(
                u'%s → %s (%d мин)' % (hms(a), hms(b), (b - a) / 60)
                for a, b in segments) or u'— заглушка не найдена'))
            sys.stdout.flush()

            dst = os.path.join(OUT_DIR, u'День %d.mp4' % day)
            intro_len = build(day, source, segments, stage, dst)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

        was, now = duration(source), duration(dst)
        print(u'День %d: %s → %s (+%.1f с заставки), %.0f МБ  %s'
              % (day, hms(was), hms(now), now - was,
                 os.path.getsize(dst) / 1048576.0,
                 u'✓' if abs(now - was - intro_len) < 1.0 else u'✗ ДЛИНА УЕХАЛА'))
        sys.stdout.flush()

    print(u'\nготовые записи: %s' % OUT_DIR)


if __name__ == '__main__':
    main(sys.argv[1:])
