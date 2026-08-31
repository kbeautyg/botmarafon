# -*- coding: utf-8 -*-
u"""Старт и запуск воронки."""
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from .. import db, keyboards, scheduler, texts

log = logging.getLogger(__name__)
router = Router(name='start')


@router.message(CommandStart())
async def on_start(message: Message):
    u"""Приветствие с кнопкой запуска.

    Клавиатуру службы заботы вешаем прямо здесь: по ТЗ она должна быть на
    виду с самого начала, ещё до того, как человек нажал «Запустить».
    """
    db.remember_user(message.from_user.id, message.from_user.username,
                     message.from_user.first_name)
    await message.answer(texts.WELCOME, reply_markup=keyboards.start_menu())


@router.message(F.chat.type == 'private', F.text == texts.LAUNCH_BUTTON)
async def on_launch_button(message: Message):
    u"""Кнопка «Запустить» под полем ввода.

    Ловим её здесь, а не в службе заботы: та забирает из лички вообще всё,
    и нажатие уехало бы менеджеру как вопрос.
    """
    user_id = message.from_user.id
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name)

    if not db.mark_launched(user_id):
        await message.answer(u'Марафон уже запущен 🙌', reply_markup=keyboards.care())
        return

    # Клавиатуру сменили на одну заботу — второй раз запускать нечего.
    await message.answer(u'Поехали! 🎁', reply_markup=keyboards.care())
    scheduler.start_chain(user_id, 'launch')
    log.info(u'воронка запущена для %s', user_id)


@router.callback_query(F.data == 'launch')
async def on_launch(call: CallbackQuery):
    u"""Кнопка «Запустить» — начало воронки.

    Повторное нажатие ничего не делает: mark_launched проходит один раз.
    Иначе человек, ткнувший кнопку дважды, получил бы две воронки разом.
    """
    user_id = call.from_user.id
    db.remember_user(user_id, call.from_user.username, call.from_user.first_name)

    if not db.mark_launched(user_id):
        await call.answer(u'Марафон уже запущен 🙌')
        return

    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:                       # сообщение могли удалить — неважно
        log.debug(u'не убрали кнопку запуска у %s', user_id)

    scheduler.start_chain(user_id, 'launch')
    log.info(u'воронка запущена для %s', user_id)
