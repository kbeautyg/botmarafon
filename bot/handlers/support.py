# -*- coding: utf-8 -*-
u"""Служба заботы и переписка команды с людьми через бота.

Пункт «Важные моменты 2» из ТЗ: кнопка заботы должна быть всегда. Заказчик
дал живой аккаунт — @Metod_Finish_Official, «ФИНИШ|СЛУЖБА ЗАБОТЫ». Ботом
туда не напишешь: телеграм запрещает ботам писать людям первыми. Поэтому
кнопка открывает человеку переписку с заботой напрямую.

Всё, что человек пишет боту, приходит команде: в чат поддержки, если он
заведён, и лично тем, кто получает заявки (Sharp, Павел, AleX). Реплай
команды — на сообщение человека, на уведомление о его запуске или на его
заявку — уходит человеку от имени бота. AleX 14.09.2026: писал людям со
своих аккаунтов, оба ушли в спам-блок; через бота такого не бывает — эти
люди сами нажимали «Запустить», и отвечать им боту Telegram разрешает.
"""
import html
import logging

from aiogram import F, Router
from aiogram.types import Message

from .. import config, contact, db, keyboards, texts

log = logging.getLogger(__name__)
router = Router(name='support')


def _who(user):
    return contact.line(user.id, user.full_name, user.username)


def _team_reply(message: Message) -> bool:
    u"""Реплай команды в личке с ботом — это ответ человеку, а не вопрос."""
    return (message.chat.type == 'private' and message.reply_to_message is not None
            and message.from_user is not None and config.is_team(message.from_user.id))


async def _relay(message: Message) -> None:
    u"""Реплай команды — человеку, к чьему сообщению или уведомлению он привязан."""
    user_id = db.care_target(message.chat.id, message.reply_to_message.message_id)
    if not user_id:
        await message.reply(u'Не понял, кому это. Отвечайте реплаем на сообщение человека, '
                            u'на уведомление о нём или на его заявку — по ним бот и находит, '
                            u'кому отправить.')
        return
    try:
        await message.bot.copy_message(user_id, message.chat.id, message.message_id)
    except Exception as err:
        await message.reply(u'Не доставили: %s' % html.escape(str(err))[:300])
        return
    log.info(u'ответ команды %s ушёл человеку %s', message.from_user.id, user_id)
    await message.reply(u'Отправлено ✅')


@router.message(F.chat.type == 'private', F.text == texts.CARE_BUTTON)
async def on_care_button(message: Message):
    db.set_care_open(message.from_user.id, True)
    await message.answer(texts.CARE_PROMPT, reply_markup=keyboards.care_link())


@router.message(_team_reply)
async def from_team(message: Message):
    u"""Реплай команды в личке с ботом — ответ человеку от имени бота."""
    await _relay(message)


async def _mirror(message: Message) -> int:
    u"""Продублировать написанное боту команде. Возвращает, скольким дошло.

    Каждое сообщение уходит двумя частями — «кто написал» и само сообщение
    копией, — и реплай на любую из них находит человека.
    """
    chats = (((config.SUPPORT_CHAT_ID,) if config.SUPPORT_CHAT_ID else ())
             + config.purchase_recipients())
    delivered = 0
    for chat in dict.fromkeys(chats):
        if chat == message.from_user.id:
            continue
        try:
            head = await message.bot.send_message(
                chat, u'💬 <b>Написали в бота</b>\n%s\n%s' % (_who(message.from_user), texts.REPLY_HINT))
            db.link_care(chat, head.message_id, message.from_user.id)
            copy = await message.bot.copy_message(chat, message.chat.id, message.message_id)
            db.link_care(chat, copy.message_id, message.from_user.id)
            delivered += 1
        except Exception as err:
            log.warning(u'сообщение %s не продублировали в %s: %s', message.from_user.id, chat, err)
    return delivered


@router.message(F.chat.type == 'private', F.text.regexp(r'^/\w'))
async def on_stray_command(message: Message):
    u"""Команда от того, кому она не открыта, — не вопрос в заботу.

    До сюда доходят только команды, которые админский роутер не взял:
    чужие /stats, /status и просто опечатки. Пересылать «/stats» в чат
    поддержки как вопрос — бессмыслица; отвечаем, что делать.
    """
    await message.answer(texts.NOT_ALLOWED_COMMAND.format(id=message.from_user.id))


@router.message(F.chat.type == 'private')
async def to_support(message: Message):
    u"""Всё, что человек пишет боту, — команде.

    Ловим любое сообщение, а не только после нажатия кнопки: человек обычно
    просто пишет вопрос в чат, ничего не нажимая. Команда, написавшая боту не
    реплаем, людям ничего не отправляет — подсказываем, как ответить.
    """
    if config.is_team(message.from_user.id):
        await message.answer(texts.TEAM_HINT)
        return

    db.remember_user(message.from_user.id, message.from_user.username,
                     message.from_user.first_name)
    delivered = await _mirror(message)
    db.set_care_open(message.from_user.id, False)
    await message.answer(texts.CARE_SENT_HERE if delivered else texts.CARE_SENT,
                         reply_markup=keyboards.care_link())


@router.message(F.reply_to_message)
async def from_support(message: Message):
    u"""Ответ менеджера реплаем в чате поддержки — обратно человеку."""
    if not config.SUPPORT_CHAT_ID or message.chat.id != config.SUPPORT_CHAT_ID:
        return
    await _relay(message)
