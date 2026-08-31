# -*- coding: utf-8 -*-
u"""Служба заботы.

Пункт «Важные моменты 2» из ТЗ: кнопка заботы должна быть всегда. Заказчик
дал живой аккаунт — @Metod_Finish_Official, «ФИНИШ|СЛУЖБА ЗАБОТЫ». Ботом
туда не напишешь: телеграм запрещает ботам писать людям первыми. Поэтому
кнопка открывает человеку переписку с заботой напрямую.

Если при этом задан чат поддержки, всё написанное боту дублируется туда:
часть людей отвечает прямо в боте, и терять эти вопросы нельзя. Менеджер
отвечает реплаем в чате — ответ возвращается человеку в бот.
"""
import html
import logging

from aiogram import F, Router
from aiogram.types import Message

from .. import config, db, keyboards, texts

log = logging.getLogger(__name__)
router = Router(name='support')


def _who(user):
    name = html.escape(user.full_name or u'без имени')
    handle = u'@%s' % user.username if user.username else u'без ника'
    return u'%s · %s · <code>%s</code>' % (name, handle, user.id)


@router.message(F.chat.type == 'private', F.text == texts.CARE_BUTTON)
async def on_care_button(message: Message):
    db.set_care_open(message.from_user.id, True)
    await message.answer(texts.CARE_PROMPT, reply_markup=keyboards.care_link())


async def _mirror_to_chat(message: Message):
    u"""Продублировать вопрос в чат поддержки, если он заведён."""
    if not config.SUPPORT_CHAT_ID:
        return

    head = await message.bot.send_message(
        config.SUPPORT_CHAT_ID, u'💬 Вопрос в заботу\n%s' % _who(message.from_user))
    db.link_care(config.SUPPORT_CHAT_ID, head.message_id, message.from_user.id)

    copy = await message.bot.copy_message(
        config.SUPPORT_CHAT_ID, message.chat.id, message.message_id)
    db.link_care(config.SUPPORT_CHAT_ID, copy.message_id, message.from_user.id)


@router.message(F.chat.type == 'private')
async def to_support(message: Message):
    u"""Всё, что человек пишет боту, — вопрос в заботу.

    Ловим любое сообщение, а не только после нажатия кнопки: человек обычно
    просто пишет вопрос в чат, ничего не нажимая.
    """
    db.remember_user(message.from_user.id, message.from_user.username,
                     message.from_user.first_name)

    await _mirror_to_chat(message)
    db.set_care_open(message.from_user.id, False)
    await message.answer(texts.CARE_SENT, reply_markup=keyboards.care_link())


@router.message(F.reply_to_message)
async def from_support(message: Message):
    u"""Ответ менеджера реплаем в чате поддержки — обратно человеку."""
    if not config.SUPPORT_CHAT_ID or message.chat.id != config.SUPPORT_CHAT_ID:
        return

    user_id = db.care_target(message.chat.id, message.reply_to_message.message_id)
    if not user_id:
        await message.reply(u'Не понял, кому это. Отвечайте реплаем на сообщение '
                            u'с вопросом — по нему бот и находит человека.')
        return

    try:
        await message.bot.copy_message(user_id, message.chat.id, message.message_id)
    except Exception as err:
        await message.reply(u'Не доставили: %s' % err)
        return
    await message.reply(u'Отправлено ✅')
