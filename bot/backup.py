# -*- coding: utf-8 -*-
u"""Запас записей дней — в закрепе у админа.

База без диска на Railway не переживает деплой: 2 сентября так пропали все
четыре записи дней, и людям ушёл только текст. Сообщение в телеграме
переживает что угодно. Поэтому после каждой загрузки бот пишет админу
служебное сообщение со всеми file_id и закрепляет его, а на старте читает
закреп и возвращает в базу то, чего там нет.

Диск это не заменяет: очередь отложенных шагов так не спасти. Но записи
дней и отзывы-картинки больше не теряются, в каком бы порядке ни делали
диск и загрузку.
"""
import json
import logging
import os

from aiogram import Bot

from . import config, db

log = logging.getLogger(__name__)

# Записи дней, загруженные в обход админского чата (tools/upload_days_bot.py):
# сам бот заливает файл через MTProto (там предел 2 ГБ, а не 50 МБ Bot API),
# а file_id кладётся сюда и приезжает с деплоем.
COMMITTED = os.path.join(config.ROOT, 'media', 'days.json')

TAG = u'#хранилище'
KEYS = tuple('day%d' % n for n in (1, 2, 3, 4)) + tuple('review%d' % n for n in range(1, 10))
KINDS = ('video', 'link', 'photo')


def dump() -> str:
    u"""Текст закрепа: по строке на каждую запись, что есть в базе."""
    lines = [TAG + u' — записи марафона. Не откреплять: отсюда бот восстановит '
             u'их после переезда.']
    for key in KEYS:
        stored = db.get_content(key)
        if stored:
            lines.append(u'%s %s %s' % (key, stored[0], stored[1]))
    return u'\n'.join(lines)


def parse(text: str) -> list[tuple[str, str, str]]:
    u"""Обратно: строки «ключ вид значение», всё остальное пропускаем."""
    found = []
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in KEYS and parts[1] in KINDS:
            found.append((parts[0], parts[1], parts[2]))
    return found


async def _pinned(bot: Bot, chat_id: int):
    u"""Закреп в чате с админом, если это наше хранилище, иначе None."""
    chat = await bot.get_chat(chat_id)
    message = chat.pinned_message
    if message and (message.text or '').startswith(TAG):
        return message
    return None


async def save(bot: Bot) -> None:
    u"""Обновить закреп у каждого админа. Беда телеграма загрузку не ломает."""
    text = dump()
    for admin in config.ADMIN_IDS:
        try:
            pinned = await _pinned(bot, admin)
            if pinned and pinned.text == text:
                continue
            if pinned:
                await bot.edit_message_text(text, chat_id=admin, message_id=pinned.message_id)
                continue
            sent = await bot.send_message(admin, text)
            await bot.pin_chat_message(admin, sent.message_id, disable_notification=True)
        except Exception as err:                       # noqa: BLE001 — любая ошибка телеграма
            log.warning(u'закреп у админа %s не обновили: %s', admin, err)


def apply_committed(path: str = COMMITTED) -> list[str]:
    u"""Поставить записи дней из media/days.json. Вернуть, какие дни сменились.

    Зачем. 10.09.2026 записи пересобрали вертикально (300–800 МБ), а залить
    их руками в админский чат было некому — людям продолжали уходить старые.
    Тогда их залил сам бот (tools/upload_days_bot.py), а file_id приехали
    сюда с коммитом.

    Каждый file_id применяется ОДИН раз: отметка committed:dayN помнит, что
    уже стояло. Поэтому если админ потом зальёт день руками, следующий
    деплой его не перетрёт — перетрёт только новый file_id в файле.
    """
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as err:
        log.warning(u'media/days.json не прочитали: %s', err)
        return []
    changed = []
    for key in ('day1', 'day2', 'day3', 'day4'):
        file_id = str((data or {}).get(key) or '').strip()
        if not file_id:
            continue
        mark = 'committed:%s' % key
        seen = db.get_content(mark)
        if seen and seen[1] == file_id:
            continue
        db.put_content(key, 'video', file_id)
        db.put_content(mark, 'committed', file_id)
        changed.append(key)
    return changed


async def restore(bot: Bot) -> int:
    u"""Вернуть в базу записи из закрепа. Что в базе уже есть — не трогаем."""
    restored = 0
    for admin in config.ADMIN_IDS:
        try:
            pinned = await _pinned(bot, admin)
        except Exception as err:                       # noqa: BLE001
            log.warning(u'закреп у админа %s не прочитали: %s', admin, err)
            continue
        for key, kind, value in parse(pinned.text if pinned else ''):
            if not db.get_content(key):
                db.put_content(key, kind, value)
                restored += 1
    return restored
