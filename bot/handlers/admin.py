# -*- coding: utf-8 -*-
u"""Админка: загрузка записей дней, проверка готовности, тестовый прогон.

Записи дней марафона весят гигабайты, и заливать их боту файлом с диска
нельзя — у ботов свой предел на отправку. Поэтому запись один раз кидают
самому боту из телеграма: он запоминает file_id и потом рассылает его
мгновенно, без перезаливки и без ограничения по размеру.
"""
import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import (backup, broadcast, config, daily, db, delivery, funnel, insights,
                keyboards, leads, live, scheduler, stats, texts)

log = logging.getLogger(__name__)
router = Router(name='admin')

DAY_TAG = re.compile(r'^day([1-4])\b', re.I)
REVIEW_TAG = re.compile(r'^review([1-9])\b', re.I)
LINK = re.compile(r'https?://\S+')

# Во сколько раз ускорить паузы в тестовом прогоне: два с половиной часа
# превращаются в две с половиной минуты, все четыре дня — минут в десять.
TEST_SPEED = 1.0 / 60


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# Команды, открытые всей команде проекта, а не только тем, кто грузит
# записи. AleX 19.09.2026: «работает команда только пульт и статс» — а
# /рассылка и /отчёт до него не доходили вовсе: роутер их не пропускал.
STATS_COMMANDS = ('stats', 'who', 'кто', 'links', 'ссылки', 'status',
                  'panel', 'пульт', 'рассылка', 'broadcast',
                  'отчет', 'отчёт', 'report', 'отмена', 'cancel',
                  'меню', 'menu', 'дошли', 'stuck', 'эфир', 'stream')


def _command(message: Message) -> str:
    u"""Имя команды без слэша и без @бота: «/stats@finish_marafon_bot 5» → stats."""
    text = (message.text or '').strip()
    if not text.startswith('/'):
        return ''
    return text.split(None, 1)[0][1:].split('@', 1)[0].lower()


def _can_stats(message: Message) -> bool:
    return bool(message.from_user) and config.can_stats(message.from_user.id, message.chat.id)


def from_admin(message: Message) -> bool:
    u"""Пропускать в этот роутер админов — и команды статистики от тех, кому
    она разрешена (config.can_stats).

    Фильтр обязателен, а не для красоты. Обработчики ниже ловят любое видео
    и любое фото — без фильтра сообщение обычного человека попадало бы сюда,
    молча отсеивалось по _is_admin и до службы заботы уже не доходило:
    aiogram останавливает разбор на первом подошедшем роутере.

    Проверяем функцией, а не F.from_user.id.in_(...): список админов должен
    читаться на каждом сообщении, иначе он застынет на моменте импорта.
    """
    if not message.from_user:
        return False
    if _is_admin(message.from_user.id):
        return True
    return _command(message) in STATS_COMMANDS and _can_stats(message)


router.message.filter(from_admin)


@router.message(Command('help', 'admin'))
async def on_help(message: Message):
    if not _is_admin(message.from_user.id):
        return
    await message.answer(texts.ADMIN_HELP)


# ------------------------------------------------------------ эфир
#
# Павел 20.09.2026 хочет вести эфиры «через бота». Саму трансляцию бот не
# ведёт — это видеочат канала; бот делает то, из-за чего эфиры и
# проваливаются: собирает людей и вовремя напоминает.

LIVE_KEY = 'live:await:%d'


async def _live_ask(message: Message) -> None:
    db.put_content(LIVE_KEY % message.chat.id, 'await', '1')
    planned = db.live_next()
    if planned:
        await message.answer(texts.LIVE_PLANNED.format(
            when=live.when_text(planned['at']), url=planned['url']))
    await message.answer(texts.LIVE_ASK)


# Не /live: эта команда с 11.09 возвращает боевые сроки после /test, и
# эфир, повешенный на неё же, её молча перехватывал (нашлось 21.09.2026).
@router.message(Command('эфир', 'stream'))
async def on_stream(message: Message):
    if not config.is_team(message.from_user.id):
        return
    await _live_ask(message)


def waiting_live(message: Message) -> bool:
    u"""Команда только что нажала «Эфир» — это сообщение с датой и ссылкой."""
    if not message.from_user or not config.is_team(message.from_user.id):
        return False
    stored = db.get_content(LIVE_KEY % message.chat.id)
    return bool(stored and stored[1]) and bool(message.text)


@router.message(waiting_live)
async def on_live_message(message: Message):
    at, url, own = live.parse(message.text)
    if not at:
        await message.answer(texts.LIVE_BAD)
        return
    db.put_content(LIVE_KEY % message.chat.id, 'await', '')
    live_id = db.live_add(url, own, at, message.from_user.id)
    count = db.broadcast_left(0)
    await message.answer(texts.LIVE_CONFIRM.format(when=live.when_text(at), count=count))
    # Показываем ровно то сообщение, которое получат люди.
    await message.answer(live.announce_text(db.live(live_id)),
                         reply_markup=keyboards.live(url))
    await message.answer(u'\u2b07\ufe0f', reply_markup=keyboards.live_confirm(live_id, count))


@router.callback_query(F.data.startswith('live:'))
async def on_live_button(call: CallbackQuery):
    if not config.is_team(call.from_user.id):
        await call.answer()
        return
    action, live_id = call.data.split(':')[1], int(call.data.split(':')[2])
    planned = db.live(live_id)
    if not planned or planned['status'] != 'ready':
        await call.answer(u'Этот эфир уже объявлен или отменён')
        return
    if action != 'me':
        # Кнопки убираем только у решения: пробу можно нажать и дважды.
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    if action == 'me':
        await call.answer(u'Присылаю пробу…')
        try:
            await call.bot.send_message(call.from_user.id,
                                        live.announce_text(planned),
                                        reply_markup=keyboards.live(planned['url']))
            await call.message.answer(texts.TRY_DONE)
        except Exception as err:
            await call.message.answer(texts.TRY_FAILED.format(why=html.escape(str(err))[:200]))
        return
    if action == 'no':
        db.live_status(live_id, 'cancelled')
        await call.answer(u'Отменено')
        await call.message.answer(texts.BROADCAST_CANCELLED)
        return
    if action == 'test':
        # Весь эфир — анонс и напоминания по часам, — но только нажавшему.
        db.live_only_for(live_id, [call.from_user.id])
        await call.answer(u'Прогон пошёл')
        await call.message.answer(texts.LIVE_TEST_STARTED.format(
            plan=_test_plan(planned['at'])))
        asyncio.create_task(live.announce(call.bot, live_id))
        return
    await call.answer(u'Объявляю…')
    asyncio.create_task(live.announce(call.bot, live_id))


def _test_plan(at: float, now: float | None = None) -> str:
    u"""Какие напоминания придут при прогоне и во сколько (по Москве)."""
    now = time.time() if now is None else now
    lines = []
    for left in live.REMINDERS:
        if now < at - left:
            moment = datetime.fromtimestamp(at - left, stats.MSK).strftime('%H:%M')
            lines.append(u'• %s — %s' % (moment, texts.LIVE_NOW if left == 0 else
                                          texts.LIVE_SOON.format(left=texts.LIVE_LEFT[left])))
    return u'\n'.join(lines) or texts.LIVE_TEST_NOTHING_LEFT


# --------------------------------------------- вопрос застрявшим
#
# Sharp 20.09.2026: «дошли». До кнопок под записью люди стояли на вопросе,
# которого не видели: кнопки приходили отдельным сообщением позже, а в
# тексте дня уже было сказано «нажми ниже». Старые сообщения Telegram не
# меняет — остаётся прислать вопрос заново.

def _stuck_by_day(people: list) -> str:
    u"""«день 1 — 12, день 2 — 3» для предпросмотра."""
    counts = {}
    for person in people:
        counts[person['poll']] = counts.get(person['poll'], 0) + 1
    return u', '.join(u'день %s — %d' % (poll.replace('day', ''), n)
                      for poll, n in sorted(counts.items()))


async def _stuck_preview(message: Message) -> None:
    u"""Сколько людей ждут кнопок — и кнопка подтверждения."""
    people = db.stuck_on_poll()
    if not people:
        await message.answer(texts.STUCK_NONE)
        return
    await message.answer(
        texts.STUCK_ASK.format(count=len(people), days=_stuck_by_day(people)),
        reply_markup=keyboards.stuck(len(people)))


@router.message(Command('дошли', 'stuck'))
async def on_stuck(message: Message):
    if not config.is_team(message.from_user.id):
        return
    await _stuck_preview(message)


@router.callback_query(F.data == 'stuck:me')
async def on_stuck_try(call: CallbackQuery):
    u"""Показать на себе, что получат застрявшие."""
    if not config.is_team(call.from_user.id):
        await call.answer()
        return
    await call.answer(u'Присылаю пробу…')
    people = db.stuck_on_poll()
    poll = people[0]['poll'] if people else 'day1'
    try:
        await call.bot.send_message(call.from_user.id, texts.POLL_QUESTIONS[poll],
                                    reply_markup=keyboards.poll(poll))
        await call.message.answer(texts.TRY_DONE)
    except Exception as err:
        await call.message.answer(texts.TRY_FAILED.format(why=html.escape(str(err))[:200]))


@router.callback_query(F.data == 'stuck:go')
async def on_stuck_go(call: CallbackQuery):
    u"""Разослать вопрос с кнопками — по одному, не торопясь."""
    if not config.is_team(call.from_user.id):
        await call.answer()
        return
    await call.answer(u'Досылаю…')
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    sent = gone = failed = skipped = 0
    for person in db.stuck_on_poll():
        uid, poll = person['user_id'], person['poll']
        # Список собран до начала рассылки, а идёт она минутами. Человек за
        # это время мог нажать кнопку под записью и уехать дальше — тогда
        # вопрос откатил бы его назад, и обе клавиатуры, старая и новая,
        # стали бы отвечать «этот вопрос уже закрыт» (аудит 20.09.2026).
        fresh = db.get_user(uid) or {}
        if fresh.get('poll') != poll or db.answered(uid, poll):
            skipped += 1
            continue
        try:
            await delivery.send_poll(call.bot, uid, poll)
        except delivery.Gone:
            db.mark_blocked(uid)
            gone += 1
            continue
        except Exception as err:
            log.warning(u'вопрос %s не дослан %s: %s', poll, uid, err)
            failed += 1
            continue
        db.log_event(uid, 'poll', poll)
        sent += 1
        await asyncio.sleep(0.05)
    log.info(u'досылка вопроса: ушло %d, ответили сами %d, закрыли бота %d, сбоев %d',
             sent, skipped, gone, failed)
    await call.message.answer(texts.STUCK_DONE.format(sent=sent, skipped=skipped,
                                                      gone=gone, failed=failed))


# ------------------------------------------------------------ меню
#
# AleX 19.09.2026: «а нельзя это кнопками всё прилепить, чтобы команду не
# называть». Команды никуда не делись — просто держать их в голове больше
# не нужно: /меню (и /start у своих) открывает всё кнопками.

def _team_message(message: Message) -> bool:
    return bool(message.from_user) and config.is_team(message.from_user.id)


@router.message(Command('меню', 'menu'))
async def on_menu(message: Message):
    if not _team_message(message):
        return
    await message.answer(texts.MENU_TITLE, reply_markup=keyboards.menu(message.chat.id))
    await message.answer(texts.MENU_HINT_KEYS, reply_markup=keyboards.team_keys())


@router.message(F.text == texts.MENU_BUTTON)
async def on_menu_button(message: Message):
    if not _team_message(message):
        return
    await message.answer(texts.MENU_TITLE, reply_markup=keyboards.menu(message.chat.id))


@router.callback_query(F.data.startswith('mn:'))
async def on_menu_button_pressed(call: CallbackQuery):
    u"""Кнопка меню — то же самое, что и команда."""
    if not config.is_team(call.from_user.id):
        await call.answer()
        return
    what = call.data.split(':', 1)[1]
    await call.answer()
    me = None

    if what == 'bc':
        if db.broadcasts_going():
            await call.message.answer(texts.BROADCAST_BUSY)
            return
        db.put_content(WAIT_KEY % call.from_user.id, 'await', '1')
        db.put_content(PICK_KEY % call.from_user.id, 'picked', '')
        await call.message.answer(texts.BROADCAST_ASK_MESSAGE)
    elif what == 'day':
        base = daily.day_number()
        await call.message.answer(daily.report([base]),
                                  reply_markup=keyboards.daily(base, [base]))
    elif what == 'stats':
        await call.message.answer(insights.render('sum', '7d'),
                                  reply_markup=keyboards.stats_menu('sum', '7d'))
    elif what == 'who':
        for part in stats.who_messages(30):
            await call.message.answer(part)
    elif what == 'live':
        await _live_ask(call.message)
    elif what == 'stuck':
        await _stuck_preview(call.message)
    elif what == 'ban':
        await call.message.answer(insights.render('bl', 'all') + texts.MENU_BAN_HINT)
    elif what in ('links', 'chats'):
        me = await call.bot.get_me()
        await call.message.answer(stats.chat_links_report(me.username) if what == 'chats'
                                  else stats.links_report(me.username))


@router.message(Command('panel', 'пульт'))
async def on_panel(message: Message):
    u"""Пульт: переписки со всеми в одном окне (AleX 17.09.2026)."""
    keys = keyboards.panel()
    if not keys:
        await message.answer(texts.PANEL_OFF)
        return
    # В рабочем чате телеграм отвергает кнопку мини-приложения, а вместе с
    # ней и всё сообщение: на /пульт из группы не приходило ничего.
    if message.chat.id < 0:
        await message.answer(texts.PANEL_IN_GROUP)
        return
    await message.answer(texts.PANEL_INTRO, reply_markup=keys)


@router.message(Command('отчет', 'отчёт', 'report'))
async def on_daily(message: Message):
    u"""Мини-отчёт за сутки — тот же, что приходит ночью сам."""
    if not _can_stats(message):
        return
    base = daily.day_number()
    await message.answer(daily.report([base]), reply_markup=keyboards.daily(base, [base]))


@router.callback_query(F.data.startswith('dl:'))
async def on_daily_button(call: CallbackQuery):
    u"""Выбрали другой день (или несколько) — пересчитать в том же сообщении."""
    if not config.can_stats(call.from_user.id, call.message.chat.id):
        await call.answer()
        return
    base, days = daily.unpack(call.data)
    try:
        await call.message.edit_text(daily.report(days),
                                     reply_markup=keyboards.daily(base, days))
    except Exception:
        # тот же набор дней — Telegram отказывается править сообщение тем же текстом
        pass
    await call.answer()


# -------------------------------------------------- рассылка всем
#
# Пишем «жду сообщение» в базу, а не в память: между командой и самим
# сообщением бот может перезапуститься, и тогда присланное видео ушло бы
# в никуда.

WAIT_KEY = 'broadcast:await:%d'


PICK_KEY = 'broadcast:picked:%d'


@router.message(Command('рассылка', 'broadcast'))
async def on_broadcast(message: Message, command: CommandObject | None = None):
    u"""Рассылка: следующее сообщение станет тем, что уйдёт людям.

    Без списка — всем. Со списком ников или ID («/рассылка @ник1 @ник2»)
    — только им: AleX 19.09.2026 просил «пачкой кому-то отправить что-то».
    """
    if not config.is_team(message.from_user.id):
        return
    if db.broadcasts_going():
        await message.answer(texts.BROADCAST_BUSY)
        return

    from .blacklist import _refs, _resolve

    refs = _refs((command.args if command else None) or u'')
    found, missing = [], []
    for ref in refs:
        user_id = _resolve(ref)
        (found if user_id and db.get_user(user_id) else missing).append(user_id or ref)
    if refs and not found:
        await message.answer(texts.BROADCAST_PICKED_NONE)
        return

    db.put_content(WAIT_KEY % message.from_user.id, 'await', '1')
    db.put_content(PICK_KEY % message.from_user.id, 'picked',
                   ','.join(str(uid) for uid in found))
    if not refs:
        await message.answer(texts.BROADCAST_ASK_MESSAGE)
        return
    note = texts.BROADCAST_PICKED_MISSING.format(
        who=html.escape(u', '.join(str(x) for x in missing))) if missing else u''
    await message.answer(texts.BROADCAST_PICKED_HEAD.format(
        found=len(found), asked=len(refs), missing=note))


@router.message(Command('отмена', 'cancel'))
async def on_cancel(message: Message):
    if not config.is_team(message.from_user.id):
        return
    db.put_content(WAIT_KEY % message.from_user.id, 'await', '')
    await message.answer(texts.BROADCAST_CANCELLED)


def waiting_broadcast(message: Message) -> bool:
    u"""Админ только что дал команду «рассылка» — это она и есть."""
    if not message.from_user or not config.is_team(message.from_user.id):
        return False
    stored = db.get_content(WAIT_KEY % message.from_user.id)
    return bool(stored and stored[1])


@router.message(waiting_broadcast)
async def on_broadcast_message(message: Message):
    u"""То, что разошлём. Показываем предпросмотр и спрашиваем подтверждение."""
    db.put_content(WAIT_KEY % message.from_user.id, 'await', '')
    stored = db.get_content(PICK_KEY % message.from_user.id)
    picked = [int(piece) for piece in ((stored[1] if stored else '') or '').split(',')
              if piece.strip().lstrip('-').isdigit()]
    db.put_content(PICK_KEY % message.from_user.id, 'picked', '')
    task_id = db.broadcast_add(message.chat.id, message.message_id,
                               message.from_user.id, picked)
    task = db.broadcast(task_id)
    await message.reply(broadcast.preview(task),
                        reply_markup=keyboards.broadcast(task_id,
                                                         db.broadcast_left(0, picked)))


@router.callback_query(F.data.startswith('bc:'))
async def on_broadcast_button(call: CallbackQuery):
    if not config.is_team(call.from_user.id):
        await call.answer()
        return
    action, task_id = call.data.split(':')[1], int(call.data.split(':')[2])
    task = db.broadcast(task_id)
    if not task or task['status'] not in ('ready',):
        await call.answer(u'Эта рассылка уже не ждёт подтверждения')
        return
    if action == 'me':
        # Проба на себе: копия того же сообщения, людям ничего не уходит.
        await call.answer(u'Присылаю пробу…')
        result = await broadcast.send_one(call.bot, call.from_user.id,
                                          task['chat_id'], task['message_id'])
        await call.message.answer(texts.TRY_DONE if result == 'ok'
                                  else texts.TRY_FAILED.format(why=result))
        return
    if action == 'no':
        db.broadcast_status(task_id, 'cancelled')
        await call.message.edit_reply_markup(reply_markup=None)
        await call.answer(u'Отменено')
        await call.message.reply(texts.BROADCAST_CANCELLED)
        return

    await call.answer(texts.BROADCAST_STARTED)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.reply(texts.BROADCAST_STARTED)
    asyncio.create_task(broadcast.run(call.bot, task_id))


@router.message(Command('day1', 'day2', 'day3', 'day4'))
async def on_day_command(message: Message, command: CommandObject):
    u"""Ждать запись дня следующим сообщением.

    Подпись к видео работает, только когда файл отправляют заново. Гораздо
    чаще запись уже лежит в каком-то чате и её пересылают — а у пересылки
    подписи нет. Тогда сначала команда, потом сама пересылка.
    """
    day = int(command.command[-1])
    db.put_content('await:%d' % message.from_user.id, 'await', str(day))
    await message.answer(u'Жду запись дня %d — пришлите или перешлите видео '
                         u'следующим сообщением.' % day)


@router.message(F.video | F.document | F.video_note)
async def on_video(message: Message):
    u"""Запись дня: видео с подписью day1…day4 либо после команды /day1."""
    if not _is_admin(message.from_user.id):
        return

    caption = (message.caption or '').strip()
    review = REVIEW_TAG.match(caption)
    if review and (message.video or message.document):
        # Видеоотзыв в ленту: подменяет шаг reviewN (см. funnel.REVIEW_SEQUENCE).
        db.put_content('review%s' % review.group(1), 'video',
                       (message.video or message.document).file_id)
        await backup.save(message.bot)
        await message.reply(u'Отзыв %s будет уходить этим видео ✅' % review.group(1))
        return

    tag = DAY_TAG.match(caption)
    waiting = db.get_content('await:%d' % message.from_user.id)
    if not tag and not (waiting and waiting[1]):
        return

    day = int(tag.group(1)) if tag else int(waiting[1])
    db.put_content('await:%d' % message.from_user.id, 'await', '')
    media = message.video or message.document or message.video_note
    db.put_content('day%d' % day, 'video', media.file_id)
    await backup.save(message.bot)
    note = u'Записал день %d ✅ Теперь он уходит людям.' % day
    if config.on_railway() and not config.db_persistent():
        note += (u'\n\nБаза не на диске — чтобы запись пережила деплой, добавьте '
                 u'в Railway переменную <code>DAY%d</code> со значением:\n'
                 u'<code>%s</code>' % (day, media.file_id))
    await message.reply(note)
    log.info(u'админ %s задал день %d', message.from_user.id, day)


@router.message(F.photo)
async def on_photo(message: Message):
    u"""Фото с подписью review1…review8 — отзыв картинкой вместо текста."""
    if not _is_admin(message.from_user.id):
        return
    tag = REVIEW_TAG.match((message.caption or '').strip())
    if not tag:
        return

    index = int(tag.group(1))
    db.put_content('review%d' % index, 'photo', message.photo[-1].file_id)
    await backup.save(message.bot)
    await message.reply(u'Отзыв %d будет уходить картинкой ✅' % index)


@router.message(F.text.regexp(r'(?i)^day[1-4]\s+https?://'))
async def on_link(message: Message):
    u"""Ссылка на запись — если дни лежат не в телеграме, а на видеосервисе."""
    if not _is_admin(message.from_user.id):
        return
    day = int(DAY_TAG.match(message.text.strip()).group(1))
    url = LINK.search(message.text).group(0)
    db.put_content('day%d' % day, 'link', url)
    await backup.save(message.bot)
    await message.reply(u'День %d будет уходить ссылкой ✅' % day)


def _content_report() -> list[str]:
    u"""Строчки про каждый день, отзывы и кружки."""
    lines = []
    for day in (1, 2, 3, 4):
        stored = db.get_content('day%d' % day)
        if not stored and config.DAY_ENV.get(day):
            lines.append(u'• День %d — из переменной DAY%d' % (day, day))
        elif not stored:
            lines.append(u'• День %d — <b>НЕ ЗАДАН</b>' % day)
        elif stored[0] == 'link':
            lines.append(u'• День %d — ссылка' % day)
        else:
            lines.append(u'• День %d — видео ✅' % day)

    on_disk = [name for name in funnel.REVIEW_SEQUENCE if delivery.review_path(name)]
    replaced = sum(1 for i in range(1, funnel.REVIEW_COUNT + 1)
                   if db.get_content('review%d' % i))
    missing = [name for name in funnel.REVIEW_SEQUENCE if name not in on_disk]
    lines.append(u'• Отзывы на диске: %d из %d%s%s' % (
        len(on_disk), funnel.REVIEW_COUNT,
        u', нет: %s' % u', '.join(missing) if missing else u'',
        u'; подменено загрузкой: %d' % replaced if replaced else u''))

    wanted = {step.ref for chain in funnel.CHAINS.values()
              for step in chain.steps if step.kind == 'circle'}
    missing = sorted(name for name in wanted
                     if not os.path.exists(os.path.join(config.CIRCLES_DIR, name + '.mp4')))
    if missing:
        lines.append(u'• Нет кружков: %s' % u', '.join(missing))
    else:
        lines.append(u'• Кружки на месте: %d' % len(wanted))

    if config.on_railway():
        lines.append(u'• База на диске ✅' if config.db_persistent() else
                     u'• База <b>НЕ НА ДИСКЕ</b> — пропадёт при деплое '
                     u'(Settings → Volumes → Add Volume, mount path /data)')
    return lines


@router.message(Command('status'))
async def on_status(message: Message):
    if not _can_stats(message):
        return
    counters = db.stats()
    lines = _content_report()
    lines.append(u'')
    lines.append(u'Людей в боте: %d, запустили марафон: %d'
                 % (counters['users'], counters['launched']))
    lines.append(u'Шагов в очереди: %d · нажали «купить»: %d (всего нажатий %d)'
                 % (counters['jobs'], counters['purchases'], counters['presses']))
    # Кого бот реально видит в доступе: правка переменных в Railway не
    # действует, пока её не применили деплоем, — отсюда это видно сразу.
    listed = lambda ids: u', '.join(str(i) for i in ids) or u'—'
    lines.append(u'')
    lines.append(u'Доступ — админы: %s' % listed(config.ADMIN_IDS))
    lines.append(u'Доступ — статистика (STATS_IDS): %s' % listed(config.STATS_IDS))
    if config.IDS_SKIPPED:
        lines.append(u'⚠️ В ADMIN_IDS/STATS_IDS бот не понял: %s — нужны числовые id '
                     u'через запятую' % html.escape(u', '.join(config.IDS_SKIPPED)))
    await message.answer(u'\n'.join(lines))


@router.message(Command('stats'))
async def on_stats(message: Message, command: CommandObject | None = None):
    u"""Подробная статистика разделами с кнопками; «/stats csv» — таблица.

    Sharp 11.09.2026: «статистика маленькая, сильно расширь». Разделы и
    расчёты — bot/insights.py.
    """
    if not _can_stats(message):
        return
    arg = ((command.args if command else '') or '').strip().lower()
    if arg in ('csv', 'таблица', 'выгрузка', 'excel'):
        await _send_csv(message.bot, message.chat.id)
        return
    await message.answer(insights.render('sum', '7'),
                         reply_markup=keyboards.stats_menu('sum', '7'))


async def _send_csv(bot, chat_id: int) -> None:
    u"""Все люди таблицей: откуда пришли, докуда дошли, ответы, покупки."""
    name = 'marathon-%s.csv' % datetime.now(insights.MSK).strftime('%Y-%m-%d')
    await bot.send_document(chat_id, BufferedInputFile(insights.csv_bytes(), filename=name),
                            caption=texts.STATS_CSV_CAPTION)


@router.callback_query(F.data.startswith('st:'))
async def on_stats_button(call: CallbackQuery):
    u"""Кнопки разделов и периодов под /stats — правят то же сообщение."""
    chat_id = call.message.chat.id if call.message else None
    if not config.can_stats(call.from_user.id, chat_id):
        await call.answer(u'Нет доступа')
        return
    parts = (call.data.split(':') + ['', ''])[:3]
    section = parts[1] if parts[1] in insights.SECTIONS or parts[1] == 'csv' else 'sum'
    period = parts[2] if parts[2] in insights.PERIODS else '7'
    if section == 'csv':
        await call.answer(u'Готовлю таблицу…')
        await _send_csv(call.bot, chat_id)
        return
    try:
        await call.message.edit_text(insights.render(section, period),
                                     reply_markup=keyboards.stats_menu(section, period))
    except Exception as err:                       # нажали ту же кнопку — текст не менялся
        if 'not modified' not in str(err):
            log.warning(u'раздел статистики %s не показали: %s', section, err)
    await call.answer()


# Команды по-русски: заказчик набирает их с телефона и латиницу не ищет.
@router.message(Command('who', 'кто'))
async def on_who(message: Message, command: CommandObject):
    u"""Поимённо, кто заходил в бота и по какой ссылке."""
    if not _can_stats(message):
        return
    asked = (command.args or '').strip()
    limit = int(asked) if asked.isdigit() and 0 < int(asked) <= 100 else 30
    for chunk in stats.who_messages(limit):
        await message.answer(chunk)


@router.message(Command('zayavki', 'заявки'))
async def on_leads(message: Message):
    u"""Кто пришёл по заявке с сайта и марафон не получал — и кнопка отправить
    (AleX 14.09.2026, подтвердил Sharp; см. bot/leads.py)."""
    if not _is_admin(message.from_user.id):
        return
    people = leads.pending()
    await message.answer(leads.report(people),
                         reply_markup=keyboards.leads_launch(len(people)) if people else None)


@router.callback_query(F.data == 'leads:launch')
async def on_leads_launch(call: CallbackQuery):
    u"""Отправка марафона прежним людям с заявки — только админ и только по кнопке."""
    if not _is_admin(call.from_user.id):
        try:
            await call.answer(u'Кнопка только для админа')
        except Exception:
            pass
        return
    try:
        await call.answer(u'Отправляю…')
    except Exception as err:                       # нажатие могло устареть
        log.debug(u'нажатие leads:launch не подтвердили: %s', err)
    try:
        await call.message.edit_reply_markup(reply_markup=None)   # второй раз не нажать
    except Exception as err:
        log.debug(u'кнопку списка людей с заявки не убрали: %s', err)
    result = await leads.launch_all(call.bot)
    log.info(u'марафон людям с заявки: %s', result)
    await call.message.answer(texts.LEADS_DONE.format(**result))


@router.message(Command('links', 'ссылки'))
async def on_links(message: Message, command: CommandObject | None = None):
    u"""Готовые ссылки с метками — вставить в рассылку.

    «/ссылки чаты» (можно с числом) — отдельный список под кнопки в рабочих
    чатах: своя ссылка на каждый чат (AleX 18.09.2026).
    """
    if not _can_stats(message):
        return
    me = await message.bot.get_me()
    args = ((command.args if command else None) or '').strip().lower()
    if args.startswith(('чат', 'chat')):
        number = re.search(r'\d+', args)
        count = min(int(number.group()), 50) if number else 20
        await message.answer(stats.chat_links_report(me.username, count))
        return
    await message.answer(stats.links_report(me.username))


@router.message(Command('resend'))
async def on_resend(message: Message):
    u"""Дослать запись дня тем, кому день ушёл без неё.

    Тревога «человеку ушёл только текст» просила «повторить отправку», а
    повторять было нечем: 2–3 сентября записи так и не дошли до людей.
    Теперь бот помнит, кому день ушёл пустым (таблица missed), и по
    /resend N шлёт им запись — только запись, текст дня они читали.

    /resend N всем — запасной ход, когда пометок нет (база пересоздана):
    запись уходит всем, кто этот день уже прошёл, судя по очереди.
    """
    if not _is_admin(message.from_user.id):
        return
    words = (message.text or '').split()
    days = [int(w) for w in words[1:] if w in ('1', '2', '3', '4')]
    if not days:
        await message.answer(u'Какой день? Например: /resend 1')
        return
    day = days[0]
    if not (db.get_content('day%d' % day) or config.DAY_ENV.get(day)):
        await message.answer(u'Запись дня %d ещё не задана — сначала пришлите '
                             u'видео или ссылку (day%d https://…).' % (day, day))
        return

    everyone = any(w.lower() in (u'всем', 'all') for w in words[1:])
    if everyone:
        targets = [uid for uid in db.launched_users()
                   if funnel.day_delivered(db.pending_chains(uid), day)]
    else:
        targets = db.missed_users(day)
    if not targets:
        await message.answer(u'День %d досылать некому: пометок «ушёл без записи» '
                             u'нет. Всем, кто день уже прошёл: /resend %d всем' % (day, day))
        return

    sent = gone = failed = 0
    for uid in targets:
        try:
            if await delivery.resend_day(message.bot, uid, day):
                sent += 1
        except delivery.Gone:
            gone += 1
            db.clear_missed(uid, day)
        except Exception as err:                       # одного не доставили — идём дальше
            failed += 1
            log.warning(u'не дослали день %d человеку %s: %s', day, uid, err)
        await asyncio.sleep(0.05)                      # лимит телеграма ~30 сообщений/с
    await message.answer(u'День %d дослал: %d чел.%s%s'
                         % (day, sent,
                            u', закрыли бота: %d' % gone if gone else u'',
                            u', не доставлено: %d' % failed if failed else u''))
    log.info(u'админ %s дослал день %d: %d/%d', message.from_user.id, day, sent, len(targets))


@router.message(Command('test'))
async def on_test(message: Message):
    u"""Прогнать всю воронку на себе, сжав паузы в шестьдесят раз."""
    if not _is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    # Админ мог ни разу не нажать /start, и строки в users для него нет.
    # Тогда все UPDATE ниже пройдут вхолостую, паузы останутся боевыми,
    # и «тестовый» прогон растянется на семь с половиной часов.
    db.remember_user(user_id, message.from_user.username, message.from_user.first_name)
    db.reset_funnel(user_id)
    db.set_speed(user_id, TEST_SPEED)
    db.mark_launched(user_id)
    scheduler.start_chain(user_id, 'launch')
    await message.answer(u'Тестовый прогон пошёл: паузы сжаты в 60 раз, '
                         u'все четыре дня займут около десяти минут.\n'
                         u'Вернуть боевые сроки для себя — /live')


@router.message(Command('live'))
async def on_live_speed(message: Message):
    u"""Вернуть себе боевые сроки после /test."""
    if not _is_admin(message.from_user.id):
        return
    db.remember_user(message.from_user.id, message.from_user.username,
                     message.from_user.first_name)
    db.set_speed(message.from_user.id, 1.0)
    db.reset_funnel(message.from_user.id)
    await message.answer(u'Боевые сроки вернул, свою очередь очистил.')
