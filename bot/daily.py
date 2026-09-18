# -*- coding: utf-8 -*-
u"""Мини-отчёт за сутки — сам приходит команде ночью.

AleX 18.09.2026: «ежедневно в 00:00 по МСК пусть приходит авто сообщение-
отчёт всем, кто в боте администратор (Павел, Алексей, Савелий): сколько
всего в этот день пришло новых, сколько из них нажали Да или Нет после
первой практики, сколько после второй, третий и четвёртый, и коротко
источники против каждого пункта цифра. И чтобы в этом сообщении можно было
выбрать сразу день или несколько дней по календарю, и отчёт обновляется».

Календарь — кнопками под самим отчётом: нажал день, отчёт пересчитался в
том же сообщении. Выбранные дни лежат в самой кнопке — смещениями от даты
отчёта, чтобы уложиться в те 64 байта, что Telegram даёт на кнопку, и
чтобы вчерашнее сообщение завтра показывало те же дни, а не съехавшие.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from . import config, db, stats, texts

log = logging.getLogger(__name__)

DAY = 86400
# Сколько дней показывать в календаре под отчётом.
CALENDAR_DAYS = 14
WEEKDAYS = (u'пн', u'вт', u'ср', u'чт', u'пт', u'сб', u'вс')


def day_number(moment: datetime | None = None) -> int:
    u"""Номер московских суток: одно число вместо даты — оно короткое."""
    moment = moment or datetime.now(stats.MSK)
    return int(moment.astimezone(stats.MSK).timestamp() // DAY)


def bounds(number: int) -> tuple[float, float]:
    u"""Начало и конец суток по Москве для номера дня."""
    start = datetime.fromtimestamp(number * DAY, stats.MSK)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp(), (midnight + timedelta(days=1)).timestamp()


def title(number: int) -> str:
    when = datetime.fromtimestamp(number * DAY, stats.MSK)
    today = day_number()
    if number == today:
        return u'сегодня, %s' % when.strftime('%d.%m')
    if number == today - 1:
        return u'вчера, %s' % when.strftime('%d.%m')
    return u'%s, %s' % (WEEKDAYS[when.weekday()], when.strftime('%d.%m'))


def _sum(days: list[int]) -> dict:
    u"""Сложить дни: за несколько суток показываем общие числа."""
    total = {'came': 0, 'launched': 0, 'left': 0, 'answers': {}, 'days': {},
             'sources': {}, 'buys': {}}
    for number in days:
        since, until = bounds(number)
        part = db.day_stats(since, until)
        total['came'] += part['came']
        total['launched'] += part['launched']
        total['left'] += part['left']
        for key, count in part['answers'].items():
            total['answers'][key] = total['answers'].get(key, 0) + count
        for day, count in part['days'].items():
            total['days'][day] = total['days'].get(day, 0) + count
        for source, count in part['sources']:
            total['sources'][source] = total['sources'].get(source, 0) + count
        for product, count in part['buys'].items():
            total['buys'][product] = total['buys'].get(product, 0) + count
    return total


def _answers_line(data: dict, day: int) -> str:
    u"""«День 1: посмотрели 12 · Да 8 · Нет 3 · молчат 1»."""
    poll = 'day%d' % day
    yes = data['answers'].get((poll, 'yes'), 0)
    no = data['answers'].get((poll, 'no'), 0)
    got = data['days'].get(day, 0)
    if day == 4:                       # после четвёртого дня вопроса нет
        return texts.DAILY_DAY4.format(got=got)
    quiet = max(0, got - yes - no)
    return texts.DAILY_DAY.format(day=day, got=got, yes=yes, no=no, quiet=quiet)


def report(days: list[int]) -> str:
    u"""Текст отчёта за выбранные сутки."""
    days = sorted(set(days))
    data = _sum(days)
    when = u', '.join(title(number) for number in days)
    sources = sorted(data['sources'].items(), key=lambda pair: (-pair[1], pair[0]))
    source_lines = u'\n'.join(u'   • %s — %d' % (stats.label(src), n) for src, n in sources)
    buys = sum(data['buys'].values())
    return texts.DAILY_REPORT.format(
        when=when,
        came=data['came'],
        launched=data['launched'],
        left=data['left'],
        days=u'\n'.join(_answers_line(data, day) for day in (1, 2, 3, 4)),
        sources=source_lines or u'   • никто не приходил',
        buys=buys)


# ------------------------------------------------------------- календарь

def pack(base: int, chosen: list[int]) -> str:
    u"""Выбранные дни — смещениями от базового: «dl:20349:0,1,5»."""
    offsets = sorted(base - number for number in set(chosen))
    return 'dl:%d:%s' % (base, ','.join(str(o) for o in offsets))


def unpack(data: str) -> tuple[int, list[int]]:
    _, base, offsets = data.split(':', 2)
    base = int(base)
    days = [base - int(o) for o in offsets.split(',') if o.strip().lstrip('-').isdigit()]
    return base, days or [base]


def when_next(now: datetime | None = None) -> float:
    u"""Сколько секунд до ближайшего времени отчёта по Москве."""
    now = (now or datetime.now(stats.MSK)).astimezone(stats.MSK)
    hour, minute = config.daily_at()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def send(bot, days: list[int] | None = None) -> int:
    u"""Разослать отчёт команде. Возвращает, скольким дошло."""
    from . import keyboards

    base = day_number()
    days = days or [base - 1]                 # ночью отчёт за прошедшие сутки
    text = report(days)
    keys = keyboards.daily(base, days)
    sent = 0
    for chat in config.report_recipients():
        try:
            await bot.send_message(chat, text, reply_markup=keys)
            sent += 1
        except Exception as err:
            log.warning(u'отчёт за день не ушёл %s: %s', chat, err)
    return sent


async def loop(bot) -> None:
    u"""Раз в сутки в заданное время — отчёт команде."""
    hour, minute = config.daily_at()
    log.info(u'ежедневный отчёт: в %02d:%02d по Москве, получателей %d',
             hour, minute, len(config.report_recipients()))
    while True:
        await asyncio.sleep(when_next())
        try:
            await send(bot)
        except Exception:
            log.exception(u'сбой ежедневного отчёта')
        await asyncio.sleep(60)               # чтобы не отправить дважды в ту же минуту
