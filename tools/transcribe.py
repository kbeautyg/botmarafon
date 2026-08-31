# -*- coding: utf-8 -*-
u"""Расшифровка присланных кружков с разметкой по ролям в воронке.

Зачем это в проекте, а не разово. Заказчик прислал одиннадцать видео с
подписями вроде «этот кружок если нажал нет» — и в одной подписи ошибся:
IMG_0683 назван третьим кружком, хотя в нём вопрос про первый день. Понять
это можно только услышав, что внутри. Расшифровка привязывает каждый файл
к его месту в сценарии, и такие расхождения видно сразу, а не на людях.

Запуск:  python tools/transcribe.py [--model small]

Кладёт РАСШИФРОВКА.md в корень проекта и tools/_transcripts.json рядом —
второй запуск берёт готовое из него и заново не считает.
"""
from __future__ import print_function

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, 'tools', 'circles.json')
CACHE = os.path.join(ROOT, 'tools', '_transcripts.json')
REPORT = os.path.join(ROOT, u'РАСШИФРОВКА.md')

# Место кружка в сценарии — то, ради чего расшифровка и делается.
ROLES = {
    'welcome_1': u'Приветствие, часть 1 · уходит сразу после кнопки «Запустить»',
    'welcome_2': u'Приветствие, часть 2 · следом за первой, потом 8 отзывов',
    'ask_day1': u'Вопрос перед опросником № 1 · через 150 минут после дня 1',
    'day2_yes': u'Ответ «Да» на опросник № 1 · следом запись дня 2',
    'day2_no': u'Ответ «Нет» на опросник № 1 · следом запись дня 2',
    'ask_day2': u'Вопрос перед опросником № 2 · через 120 минут после дня 2',
    'day3_yes': u'Ответ «Да» на опросник № 2 · следом запись дня 3',
    'day3_no': u'Ответ «Нет» на опросник № 2 · следом запись дня 3',
    'day4_yes': u'Ответ «Да» на опросник № 3 · следом запись дня 4',
    'day4_no': u'Ответ «Нет» на опросник № 3 · следом запись дня 4',
    'offer': u'Перед кнопками покупки · через 60 минут после дня 4',
    'care': u'После кнопок покупки · про службу заботы',
}


# Расхождения между тем, как файл подписан в переписке, и тем, что в нём
# слышно. Найдены сверкой расшифровки с ТЗ; держим их в отчёте, чтобы
# заказчику было видно, на каком основании принято решение.
NOTES = [
    (u'IMG_0683 подписан «Третий кружок», а это первый опросник',
     u'В пункте 3 ТЗ он назван «кружок номер 2», в сообщении перед файлом — '
     u'«Третий кружок». Внутри звучит «ты уже успел посмотреть **первый** день '
     u'нашего марафона?» — значит это вопрос перед опросником № 1. Так и '
     u'поставлен в бота.'),
    (u'Третий день: «примерно через три часа» против 120 минут',
     u'В кружке `day2_yes` Павел говорит «третий день пришлю примерно через три '
     u'часа». В ТЗ (пункт 4) — «ровно через 120 минут», в тексте под вторым днём '
     u'— «через 2 часа». В боте стоит 120 минут, как в ТЗ. Нужно сверить.'),
    (u'Приветствие длиннее лимита Telegram',
     u'IMG_0680 идёт 1:23, а в кружок пишется не больше минуты. Резать середину '
     u'нельзя: там и метод, и анонс первого дня, и анонс отзывов. Разрезано на '
     u'0:40 по концу фразы «…покажи моих родственников», части уходят подряд.'),
    (u'Акция 48 часов нигде в ТЗ не описана',
     u'В кружке `offer`: «у тебя есть ещё 48 часов» и «все 48 часов действует '
     u'акция». Сроков и напоминаний в ТЗ нет — бот про них только упоминает '
     u'в подписи к кнопкам.'),
]


def load_conf():
    with io.open(CONF, encoding='utf-8') as handle:
        return json.load(handle)


def duration(path):
    raw = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                   'format=duration', '-of', 'csv=p=0', path])
    return float(raw.decode('utf-8').strip())


def transcribe(model, path):
    u"""Текст с таймкодами. Звук выдёргиваем в моно 16 кГц — whisper хочет его."""
    from faster_whisper import WhisperModel                       # noqa: F401

    handle, wav = tempfile.mkstemp(suffix='.wav')
    os.close(handle)
    try:
        subprocess.check_call(['ffmpeg', '-v', 'error', '-i', path, '-vn',
                               '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
                               wav, '-y'])
        segments, _ = model.transcribe(
            wav, language='ru', vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=400))
        return [{'s': round(seg.start, 2), 'e': round(seg.end, 2),
                 't': seg.text.strip()} for seg in segments]
    finally:
        os.remove(wav)


def collect(conf, model_name):
    u"""Расшифровки всех исходников: из кэша либо заново."""
    cached = {}
    if os.path.exists(CACHE):
        with io.open(CACHE, encoding='utf-8') as handle:
            cached = json.load(handle)

    sources = []
    for item in conf['circles']:
        if item['src'] not in sources:
            sources.append(item['src'])

    todo = [name for name in sources if name not in cached]
    model = None
    if todo:
        from faster_whisper import WhisperModel
        print(u'считаем модель %s, файлов: %d' % (model_name, len(todo)))
        model = WhisperModel(model_name, device='cpu', compute_type='int8')

    for name in todo:
        path = os.path.join(conf['src_dir'], name + '.MOV')
        if not os.path.exists(path):
            print(u'нет исходника %s — пропускаем' % name)
            continue
        print(u'  %s…' % name, end='')
        sys.stdout.flush()
        cached[name] = {'duration': round(duration(path), 2),
                        'segments': transcribe(model, path)}
        print(u' готово')

    with io.open(CACHE, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(cached, ensure_ascii=False, indent=1))
    return cached


def clock(seconds):
    return u'%d:%02d' % (int(seconds) // 60, int(seconds) % 60)


def piece_text(record, item):
    u"""Реплики, попадающие в кусок исходника, из которого сделан кружок."""
    start, end = item.get('cut', [0, record['duration']])
    return [seg for seg in record['segments']
            if seg['e'] > start + 0.2 and seg['s'] < end - 0.2]


def report(conf, data):
    lines = [u'# Расшифровка присланных кружков', u'']
    lines.append(u'Одиннадцать видео от заказчика, разобранные по ролям в воронке. '
                 u'Расшифровано автоматически (`python tools/transcribe.py`), '
                 u'знаки препинания расставлены распознаванием.')
    lines.append(u'')
    lines.append(u'## Что где')
    lines.append(u'')
    lines.append(u'| Исходник | В боте | Длина | Роль |')
    lines.append(u'|---|---|---|---|')

    for item in conf['circles']:
        record = data.get(item['src'])
        if not record:
            continue
        start, end = item.get('cut', [0, record['duration']])
        lines.append(u'| %s.MOV | `%s` | %s | %s |'
                     % (item['src'], item['name'], clock(end - start),
                        ROLES.get(item['name'], u'—')))

    for name, why in conf.get('missing', {}).items():
        lines.append(u'| — | `%s` | — | **НЕ ПРИСЛАН.** %s |' % (name, why))

    lines.append(u'')
    lines.append(u'## Что вскрыла расшифровка')
    lines.append(u'')
    for title, body in NOTES:
        lines.append(u'**%s.** %s' % (title, body))
        lines.append(u'')

    lines.append(u'## Что говорится')
    lines.append(u'')

    for item in conf['circles']:
        record = data.get(item['src'])
        if not record:
            continue
        lines.append(u'### `%s` — %s' % (item['name'], ROLES.get(item['name'], u'')))
        lines.append(u'')
        cut = item.get('cut')
        if cut:
            lines.append(u'Из **%s.MOV** (%s), кусок %s–%s: исходник длиннее минуты, '
                         u'а в кружок Telegram пишет не больше.'
                         % (item['src'], clock(record['duration']),
                            clock(cut[0]), clock(cut[1])))
        else:
            lines.append(u'Файл **%s.MOV**, %s.' % (item['src'], clock(record['duration'])))
        lines.append(u'')
        for seg in piece_text(record, item):
            lines.append(u'- `%s` %s' % (clock(seg['s']), seg['t']))
        lines.append(u'')

    return u'\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='small',
                        help=u'модель whisper: tiny/base/small/medium')
    args = parser.parse_args()

    conf = load_conf()
    data = collect(conf, args.model)
    with io.open(REPORT, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(report(conf, data))
    print(u'готово: %s' % REPORT)


if __name__ == '__main__':
    main()
