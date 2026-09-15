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

2. Ссылка из заявки с сайта — ?start=zayavka57. С 14.09.2026 марафон
   запускается и такому человеку (AleX, Sharp); до этого он только ждал
   менеджера по спортзалу. Смысл ссылки в том, что
   ПЕРВЫМ пишет человек — нажатие «Запустить» и есть его первое
   сообщение. Менеджеру больше не надо писать незнакомому аккаунту, за
   что телеграм ограничивал аккаунт (Павел 09.09.2026).
"""
import html
import logging
import re
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from .. import config, contact, db, keyboards, launch_report, scheduler, stats, texts

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
    return contact.line(user.id, user.full_name, user.username)


def _nth(n: int) -> str:
    return u'в первый раз' if n <= 1 else u'%d-й раз' % n


def _entry_text(user, template: str, **extra) -> str:
    known = db.get_user(user.id) or {}
    when = datetime.fromtimestamp(time.time(), stats.MSK).strftime('%d.%m %H:%M')
    return template.format(who=_who(user), when=when,
                           source=stats.source_label(known), **extra)


async def _announce(bot, user, text: str, skip: tuple = ()) -> None:
    u"""Уведомление о входе — всем из config.entry_recipients(). Сбой одного
    получателя не мешает остальным и человека в боте не касается."""
    for chat in config.entry_recipients():
        if not chat or chat in skip:
            continue
        try:
            sent = await bot.send_message(chat, text)
        except Exception as err:
            log.warning(u'уведомление о входе %s не ушло в %s: %s', user.id, chat, err)
            continue
        # реплай на уведомление — сообщение этому человеку (handlers/support.py)
        if sent is not None:
            db.link_care(chat, sent.message_id, user.id)


async def _lead_arrived(message: Message, number: str, gift: bool = True) -> None:
    u"""Человек с заявкой открыл бота: поздороваться, позвать менеджера и —
    если марафон только что запущен — сказать про подарок."""
    no = u' №%s' % number if number else u''
    hello = texts.LEAD_HELLO if gift else texts.LEAD_HELLO_AGAIN
    await message.answer(hello.format(no=no), reply_markup=keyboards.care())
    if not config.SUPPORT_CHAT_ID:
        log.warning(u'заявка%s пришла в бота, а SUPPORT_CHAT_ID не задан', no)
        return
    # Тем же мостом, что и служба заботы: менеджер отвечает реплаом на это
    # сообщение, и ответ уходит человеку в бота (handlers/support.py).
    head = await message.bot.send_message(
        config.SUPPORT_CHAT_ID,
        texts.LEAD_TO_SUPPORT.format(no=no, who=_who(message.from_user)))
    db.link_care(config.SUPPORT_CHAT_ID, head.message_id, message.from_user.id)


# Кому приветствие ещё уходит, а марафон не встал в очередь: второй /start
# из той же пачки обновлений (после выкладки) иначе увидел бы пустую очередь
# и сказал бы новичку «новых дней нет» (ревью 11.09.2026).
_launching: set[int] = set()


def _repeat_start_text(user_id: int) -> str:
    u"""Ответ на повторный /start — по правде о том, что человеку предстоит.

    Очередь не пуста или приветствие ещё уходит — марафон идёт. Очереди нет,
    но открыт вопрос «посмотрел?» (закрывал бота на вопросе и вернулся) —
    вопрос приходит заново: ответ продолжит марафон с того же места, а
    «пройти заново» стёрло бы весь путь. Иначе честно: новых дней нет.
    """
    if user_id in _launching or db.pending_chains(user_id):
        return texts.ALREADY_RUNNING
    poll = (db.get_user(user_id) or {}).get('poll')
    if poll and scheduler.requeue_poll(user_id, poll):
        return texts.ALREADY_RUNNING
    return texts.NOTHING_SCHEDULED


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject | None = None):
    user_id = message.from_user.id
    # site_fb--k3v9x0a1b2c4: хвост — код клика на сайте, источник — до него
    raw, click = launch_report.split(_payload(message, command).strip().lower())
    lead = LEAD_PAYLOAD.match(raw)
    source = LEAD_SOURCE if lead else stats.parse_source(raw)
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name, source)

    if lead:
        if lead.group(1):
            db.set_lead_no(user_id, int(lead.group(1)))
        # С 14.09.2026 марафон запускается и людям с заявки (AleX, Sharp): до
        # этого они только ждали менеджера. В очередь — до любого обращения к
        # Telegram: сбой приветствия не должен отменить марафон.
        launched = db.mark_launched(user_id)
        nth = 0
        if launched:
            scheduler.start_chain(user_id, 'launch')
            nth = db.count_launch(user_id)
        try:
            await _lead_arrived(message, lead.group(1), gift=launched)
        finally:
            log.info(u'заявка с сайта: человек %s открыл бота, марафон %s',
                     user_id, u'запущен' if launched else u'уже шёл')
            # в чат заботы заявка уже упала — туда второй раз не шлём
            await _announce(message.bot, message.from_user,
                            _entry_text(message.from_user,
                                        texts.ENTRY_LEAD if launched else texts.ENTRY_LEAD_AGAIN,
                                        nth=_nth(nth)),
                            skip=(config.SUPPORT_CHAT_ID,))
        return

    # Повторный /start воронку не удваивает: mark_launched проходит один раз.
    if not db.mark_launched(user_id):
        await message.answer(_repeat_start_text(user_id), reply_markup=keyboards.restart())
        return

    _launching.add(user_id)
    try:
        await message.answer(texts.START_TEXT, reply_markup=keyboards.care())
    finally:
        # Марафон встаёт в очередь в любом случае: сбой этого сообщения
        # оставлял человека «запущенным» без единого шага (ревью 11.09.2026).
        # Отметку снимаем первой: упадёт постановка — повторный /start скажет
        # правду, а не «придёт по расписанию».
        _launching.discard(user_id)
        scheduler.start_chain(user_id, 'launch')
        nth = db.count_launch(user_id)
        log.info(u'воронка запущена для %s', user_id)
        # Первый запуск по ссылке с сайта — сайту, для рекламы Meta. Повторный
        # /start сюда не доходит: запуск считается один раз на человека.
        launch_report.report(click)
        # Уведомление тоже здесь: даже если приветствие не ушло, вход был и
        # марафон запущен — команда должна это видеть (AleX 11.09.2026).
        await _announce(message.bot, message.from_user,
                        _entry_text(message.from_user, texts.ENTRY_LAUNCHED, nth=_nth(nth)))


@router.callback_query(F.data == 'restart')
async def on_restart(call: CallbackQuery):
    u"""«Пройти заново»: снять старые шаги и запустить цепочку с первого дня."""
    user_id = call.from_user.id
    db.reset_funnel(user_id)
    db.mark_launched(user_id)
    scheduler.start_chain(user_id, 'launch')
    nth = db.count_launch(user_id)
    # Кнопку убираем, чтобы её не нажали второй раз и не задвоили воронку.
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception as err:                       # сообщение могли удалить
        log.debug(u'клавиатуру перезапуска не убрали: %s', err)
    await call.message.answer(texts.RESTART_DONE, reply_markup=keyboards.care())
    await call.answer()
    log.info(u'воронка перезапущена по кнопке для %s', user_id)
    await _announce(call.bot, call.from_user,
                    _entry_text(call.from_user, texts.ENTRY_LAUNCHED, nth=_nth(nth)))
