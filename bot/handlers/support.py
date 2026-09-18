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
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import chatlog, config, contact, db, delivery, keyboards, texts

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
    chatlog.save(message, user_id, 'out', message.from_user.id)
    log.info(u'ответ команды %s ушёл человеку %s', message.from_user.id, user_id)
    await message.reply(u'Отправлено ✅')


@router.message(F.chat.type == 'private', F.text == texts.CARE_BUTTON)
async def on_care_button(message: Message):
    db.set_care_open(message.from_user.id, True)
    await message.answer(texts.CARE_PROMPT, reply_markup=keyboards.care_link())


# ------------------------------------------------ /написать по ID или нику
#
# Sharp 15.09.2026: реплай работает только на уведомления, пришедшие после
# выкладки моста; людям со старых уведомлений и тем, чьё уведомление
# потерялось в переписке, писать нечем. Команда находит человека по ID
# или нику из базы бота — написать можно только тому, кто сам запускал бота.

WRITE_COMMANDS = ('написать', 'write')
# «/написать @ник текст»: команда, кому, дальше текст — хоть в несколько строк
WRITE_HEAD = re.compile(r'\s*/\S+\s*(\S*)\s*')
USERNAME = re.compile(r'^(?:@|(?:https?://)?t\.me/)?([A-Za-z][A-Za-z0-9_]{3,31})$', re.I)


def _team_private(message: Message) -> bool:
    return (message.chat.type == 'private' and message.from_user is not None
            and config.is_team(message.from_user.id))


def _find(ref: str) -> dict | None:
    u"""Человек из базы бота по ID, @нику или ссылке t.me/ник."""
    if ref.isdigit():
        return db.get_user(int(ref))
    found = USERNAME.match(ref)
    return db.find_by_username(found.group(1)) if found else None


def _body(message: Message, start: int) -> tuple[str, list | None]:
    u"""Текст после «/написать кому» — с сохранённым оформлением.

    Смещения сущностей (жирный, ссылки) Telegram считает в UTF-16, а не в
    символах Python: эмодзи занимают две единицы. Сдвигаем на длину головы.
    """
    text = message.text or ''
    shift = len(text[:start].encode('utf-16-le')) // 2
    entities = [e.model_copy(update={'offset': e.offset - shift})
                for e in (getattr(message, 'entities', None) or []) if e.offset >= shift]
    return text[start:], entities or None


# AleX 17.09.2026 написал боту «@nataliya_famme : Добрый день!…» — без команды.
# Так и пишут: ник в начале, дальше текст (двоеточие или тире — по желанию).
NICK_HEAD = re.compile(r'\s*(@[A-Za-z][A-Za-z0-9_]{3,31})\s*[:：,—–-]?\s*')


@router.message(Command(*WRITE_COMMANDS), _team_private)
async def on_write(message: Message):
    u"""/написать 391182739 текст — сообщение человеку от имени бота."""
    head = WRITE_HEAD.match(message.text or '')
    ref = head.group(1) if head else ''
    if not ref:
        await message.answer(texts.WRITE_USAGE)
        return
    await _write(message, ref, head.end())


def _nick_message(message: Message) -> bool:
    u"""Команда пишет боту «@ник текст» — это сообщение человеку, а не вопрос."""
    head = NICK_HEAD.match(message.text or '')
    return (_team_private(message) and head is not None
            and bool((message.text or '')[head.end():].strip()))


@router.message(_nick_message)
async def on_nick_message(message: Message):
    head = NICK_HEAD.match(message.text)
    await _write(message, head.group(1), head.end())


async def _write(message: Message, ref: str, start: int) -> None:
    u"""Найти человека по ref и отправить ему текст с позиции start."""
    person = _find(ref)
    if not person:
        await message.answer(texts.WRITE_NOT_FOUND.format(ref=html.escape(ref)))
        return

    user_id = person['user_id']
    who = contact.line(user_id, person.get('first_name'), person.get('username'))
    body, entities = _body(message, start)
    if not body.strip():
        # Без текста — спросить, что отправить: реплаем пройдёт и фото, и голосовое.
        ask = await message.answer(texts.WRITE_ASK.format(who=who))
        db.link_care(message.chat.id, ask.message_id, user_id)
        return

    try:
        await delivery._guard(message.bot.send_message(
            user_id, body, entities=entities, parse_mode=None))
    except delivery.Gone:
        db.mark_blocked(user_id)
        await message.answer(texts.WRITE_GONE.format(who=who))
        return
    except Exception as err:
        await message.answer(texts.WRITE_FAILED.format(who=who, why=html.escape(str(err))[:300]))
        return
    chatlog.save_text(user_id, body, message.from_user.id)
    log.info(u'команда %s написала человеку %s по /написать', message.from_user.id, user_id)
    sent = await message.answer(texts.WRITE_SENT.format(who=who))
    db.link_care(message.chat.id, sent.message_id, user_id)


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
            head = await delivery.note(
                message.bot, chat,
                u'💬 <b>Написали в бота</b>\n%s\n%s' % (_who(message.from_user), texts.REPLY_HINT),
                keyboards.ban_ask(message.from_user.id, chat),
                keyboards.ban_ask(message.from_user.id))
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
    chatlog.save(message, message.from_user.id, 'in')
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
