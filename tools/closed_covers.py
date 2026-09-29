# -*- coding: utf-8 -*-
u"""Картинки «запись закрыта» — для записей дней после марафона.

Через три дня после кнопок покупки бот закрывает записи дней (bot/closing.py):
видео в сообщении заменяется картинкой. Картинка — обложка того же дня
(media/intro/coverN.jpg): заголовок «День N: …» остаётся чётким, остальное
размыто и притемнено, поверх — замок и «Запись закрыта».

Запуск из корня проекта (нужен Pillow):

    python tools/closed_covers.py

Результат — media/closed/day1…day4.jpg; их коммитят вместе с кодом.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERS = os.path.join(ROOT, 'media', 'intro')
OUT = os.path.join(ROOT, 'media', 'closed')

HEADER = 104                     # высота полосы «День N: …» сверху обложки
GOLD = (242, 196, 110)
CREAM = (240, 228, 205)
FONTS = 'C:/Windows/Fonts'

TITLE = u'ЗАПИСЬ ЗАКРЫТА'
NOTE = (u'Записи марафона открыты', u'три дня после его финала')


def font(names, size):
    for name in names:
        path = os.path.join(FONTS, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    sys.exit(u'нет шрифта: %s' % u', '.join(names))


def glow(size, draw_on, radius=14, strength=2):
    u"""Слой свечения: то же, что нарисовано, только размытое."""
    layer = Image.new('RGBA', size, (0, 0, 0, 0))
    draw_on(ImageDraw.Draw(layer))
    blurred = layer.filter(ImageFilter.GaussianBlur(radius))
    for _ in range(strength - 1):
        blurred = Image.alpha_composite(blurred, blurred)
    return blurred


def lock(draw, cx, top, color, width=12):
    u"""Замок: дужка полукругом и корпус со скважиной."""
    body_w, body_h, arc_r = 170, 132, 52
    body_top = top + arc_r + 30
    draw.arc((cx - arc_r, top, cx + arc_r, top + 2 * arc_r), 180, 360, fill=color, width=width)
    draw.line((cx - arc_r + width // 2, top + arc_r, cx - arc_r + width // 2, body_top),
              fill=color, width=width)
    draw.line((cx + arc_r - width // 2, top + arc_r, cx + arc_r - width // 2, body_top),
              fill=color, width=width)
    draw.rounded_rectangle((cx - body_w // 2, body_top, cx + body_w // 2, body_top + body_h),
                           radius=22, fill=color)
    hole = (0, 0, 0, 255) if len(color) == 4 else (20, 16, 12)
    draw.ellipse((cx - 16, body_top + 38, cx + 16, body_top + 70), fill=hole)
    draw.rectangle((cx - 6, body_top + 62, cx + 6, body_top + 98), fill=hole)
    return body_top + body_h


def build(day: int) -> str:
    cover = Image.open(os.path.join(COVERS, 'cover%d.jpg' % day)).convert('RGB')
    w, h = cover.size
    # Всё, кроме заголовка дня, — размыть и притемнить; заголовок остаётся
    # чётким и плавно, без шва, уходит в размытое.
    canvas = Image.eval(cover.filter(ImageFilter.GaussianBlur(22)), lambda v: int(v * 0.33))
    mask = Image.new('L', (w, h), 0)
    fade_from, fade_to = HEADER - 22, HEADER + 18
    for y in range(fade_to):
        level = 255 if y < fade_from else int(255 * (fade_to - y) / float(fade_to - fade_from))
        ImageDraw.Draw(mask).line((0, y, w, y), fill=level)
    canvas.paste(cover, (0, 0), mask)
    canvas = canvas.convert('RGBA')

    title_font = font(('cambriab.ttf', 'georgiab.ttf', 'timesbd.ttf'), 60)
    note_font = font(('segoeui.ttf', 'calibri.ttf', 'arial.ttf'), 32)
    top = int(h * 0.36)

    def draw_all(d, color=GOLD, note=CREAM):
        bottom = lock(d, w // 2, top, color + (255,))
        tw = d.textlength(TITLE, font=title_font)
        d.text(((w - tw) / 2, bottom + 58), TITLE, font=title_font, fill=color + (255,))
        y = bottom + 58 + 92
        for line in NOTE:
            lw = d.textlength(line, font=note_font)
            d.text(((w - lw) / 2, y), line, font=note_font, fill=note + (255,))
            y += 46

    canvas.alpha_composite(glow(canvas.size, lambda d: draw_all(d, GOLD, GOLD), radius=18))
    sharp = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    draw_all(ImageDraw.Draw(sharp))
    canvas.alpha_composite(sharp)

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, 'day%d.jpg' % day)
    canvas.convert('RGB').save(path, quality=86, optimize=True, progressive=True)
    return path


if __name__ == '__main__':
    for number in (1, 2, 3, 4):
        print(build(number))
