# -*- coding: utf-8 -*-
u"""Старт — и сразу воронка.

Заказчик 03.09.2026: приветственный текст стоит ДО кнопки «Старт» — в
описании бота на пустом экране (main.py задаёт его через API), — а кнопка
«Запустить» после старта лишняя: «старт нажал — и пошло-поехало,
кружочки». Поэтому /start сразу запускает цепочку. Одно короткое сообщение
всё же уходит: к нему привязана клавиатура со службой заботы, которая по
ТЗ должна быть под полем ввода всегда.
"""
import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from .. import db, keyboards, scheduler, stats, texts

log = logging.getLogger(__name__)
router = Router(name='start')


def _payload(message: Message, command: CommandObject | None) -> str:
    u"""Хвост ссылки t.me/бот?start=ig — Telegram присылает его как «/start ig»."""
    if command is not None and command.args:
        return command.args
    parts = (message.text or '').split(None, 1)
    return parts[1] if len(parts) > 1 else ''


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject | None = None):
    user_id = message.from_user.id
    source = stats.parse_source(_payload(message, command))
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name, source)

    # Повторный /start воронку не удваивает: mark_launched проходит один раз.
    if not db.mark_launched(user_id):
        await message.answer(texts.ALREADY_RUNNING, reply_markup=keyboards.care())
        return

    await message.answer(texts.START_TEXT, reply_markup=keyboards.care())
    scheduler.start_chain(user_id, 'launch')
    log.info(u'воронка запущена для %s', user_id)
