# -*- coding: utf-8 -*-
u"""Старт — и сразу воронка.

Заказчик 03.09.2026: приветственный текст стоит ДО кнопки «Старт» — в
описании бота на пустом экране (main.py задаёт его через API), — а кнопка
«Запустить» после старта лишняя: «старт нажал — и пошло-поехало,
кружочки». Поэтому /start сразу запускает цепочку. Одно короткое сообщение
всё же уходит: к нему привязана клавиатура со службой заботы, которая по
ТЗ должна быть под полем ввода всегда.

Два особых случая:

1. Повторный /start. Воронку он не удваивает — но и молчать нельзя:
   человек, очистивший переписку, оставался ни с чем (AleX 09.09.2026).
   Даём кнопку «пройти заново».

2. Ссылка из заявки с сайта — ?start=zayavka57. Марафон такому человеку
   не запускаем: он ждёт менеджера по спортзалу. Смысл ссылки в том, что
   ПЕРВЫМ пишет человек — нажатие «Запустить» и есть его первое
   сообщение. Менеджеру больше не надо писать незнакомому аккаунту, за
   что телеграм ограничивал аккаунт (Павел 09.09.2026).
"""
import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from .. import config, db, keyboards, scheduler, stats, texts

log = logging.getLogger(__name__)
router = Router(name='start')

# ?start=zayavka57 — заявка №57 с сайта; номер необязателен.
LEAD_PAYLOAD = re.compile(r'^zayavka(\d{0,6})$')
LEAD_SOURCE = 'zayavka'


def _payload(message: Message, command: CommandObject | None) -> str:
    u"""Хвост ссылки t.me/бот?start=ig — Telegram присылает его как «/start ig»."""
    if command is not None and command.args:
        return command.args
    parts = (message.text or '').split(None, 1)
    return parts[1] if len(parts) > 1 else ''


def _who(user) -> str:
    name = html.escape(user.full_name or u'без имени')
    handle = u'@%s' % user.username if user.username else u'без ника'
    return u'%s · %s · <code>%s</code>' % (name, handle, user.id)


async def _lead_arrived(message: Message, number: str) -> None:
    u"""Человек с заявкой открыл бота: поздороваться и позвать менеджера."""
    no = u' №%s' % number if number else u''
    await message.answer(texts.LEAD_HELLO.format(no=no), reply_markup=keyboards.care())
    if not config.SUPPORT_CHAT_ID:
        log.warning(u'заявка%s пришла в бота, а SUPPORT_CHAT_ID не задан', no)
        return
    # Тем же мостом, что и служба заботы: менеджер отвечает реплаом на это
    # сообщение, и ответ уходит человеку в бота (handlers/support.py).
    head = await message.bot.send_message(
        config.SUPPORT_CHAT_ID,
        texts.LEAD_TO_SUPPORT.format(no=no, who=_who(message.from_user)))
    db.link_care(config.SUPPORT_CHAT_ID, head.message_id, message.from_user.id)


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject | None = None):
    user_id = message.from_user.id
    raw = _payload(message, command).strip().lower()
    lead = LEAD_PAYLOAD.match(raw)
    source = LEAD_SOURCE if lead else stats.parse_source(raw)
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name, source)

    if lead:
        await _lead_arrived(message, lead.group(1))
        log.info(u'заявка с сайта: человек %s открыл бота', user_id)
        return

    # Повторный /start воронку не удваивает: mark_launched проходит один раз.
    if not db.mark_launched(user_id):
        await message.answer(texts.ALREADY_RUNNING, reply_markup=keyboards.restart())
        return

    await message.answer(texts.START_TEXT, reply_markup=keyboards.care())
    scheduler.start_chain(user_id, 'launch')
    log.info(u'воронка запущена для %s', user_id)


@router.callback_query(F.data == 'restart')
async def on_restart(call: CallbackQuery):
    u"""«Пройти заново»: снять старые шаги и запустить цепочку с первого дня."""
    user_id = call.from_user.id
    db.reset_funnel(user_id)
    db.mark_launched(user_id)
    scheduler.start_chain(user_id, 'launch')
    # Кнопку убираем, чтобы её не нажали второй раз и не задвоили воронку.
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as err:                       # сообщение могли удалить
        log.debug(u'клавиатуру перезапуска не убрали: %s', err)
    await call.message.answer(texts.RESTART_DONE, reply_markup=keyboards.care())
    await call.answer()
    log.info(u'воронка перезапущена по кнопке для %s', user_id)
