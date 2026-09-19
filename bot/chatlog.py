# -*- coding: utf-8 -*-
u"""Что сохранять из переписки команды с человеком.

Пульту админа (bot/web.py) нужна история: открыл человека — видишь весь
диалог. Раньше сообщения только пересылались команде в личку и нигде не
оставались, поэтому переписка жила ровно до того, как уедет вверх лента
уведомлений.

Сохраняем саму суть: кто писал, когда, текст или подпись, и какого вида
вложение. Файлы не копируем — храним file_id, по нему бот всегда может
переслать вложение заново.
"""
from . import db

# Что бот умеет распознать во входящем сообщении: поле сообщения → вид.
KINDS = ('photo', 'video', 'video_note', 'voice', 'audio', 'document',
         'sticker', 'animation')


def parts(message) -> tuple[str, str | None, str | None]:
    u"""(вид, текст, file_id) из сообщения Telegram."""
    for kind in KINDS:
        media = getattr(message, kind, None)
        if not media:
            continue
        # У фото Telegram отдаёт список размеров — берём самый крупный.
        if isinstance(media, (list, tuple)):
            media = media[-1]
        return kind, message.caption, getattr(media, 'file_id', None)
    return 'text', message.text or message.caption, None


def save(message, user_id: int, side: str, author: int | None = None,
         tg_id: int | None = None) -> None:
    u"""Записать сообщение переписки. Сбой записи не должен ронять доставку."""
    kind, text, file_id = parts(message)
    db.save_message(user_id, side, kind, text, file_id, author, tg_id)


def save_text(user_id: int, text: str, author: int | None = None,
              tg_id: int | None = None) -> None:
    db.save_message(user_id, 'out', 'text', text, None, author, tg_id)
