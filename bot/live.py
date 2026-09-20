# -*- coding: utf-8 -*-
u"""Эфир: бот собирает людей и приводит их на трансляцию.

Павел 20.09.2026 хочет проводить эфиры «через бота». Сам бот вести
трансляцию не может — у Telegram это видеочат канала или группы, а не
возможность бота. Зато всё остальное, из-за чего эфиры и проваливаются,
бот делает лучше человека: объявляет о начале всем сразу, напоминает за
час и за десять минут, и в ту же секунду, когда эфир начался, даёт кнопку
«Смотреть». Люди не пропускают, а команда не сидит с таймером.

Ссылка — любая: видеочат канала (t.me/канал?livestream), приглашение в
группу, VK или YouTube. Бот её не проверяет на вид трансляции: он ведёт
людей туда, куда сказали.

Напоминания идут по базе, а не по таймерам в памяти: между анонсом и
эфиром бот может перезапуститься (деплой), и расписание должно это
пережить.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from . import config, db, delivery, keyboards, stats, texts

log = logging.getLogger(__name__)

# За сколько до начала напоминать. Час — чтобы успел спланировать вечер,
# десять минут — чтобы дошёл до телефона, ноль — «началось, заходи».
REMINDERS = (3600, 600, 0)
EVERY = 30                       # как часто смотреть, не пора ли напомнить
PER_SECOND = 20                  # с той же скоростью, что рассылка


def parse(text: str, now: float | None = None) -> tuple:
    u"""Разобрать «20.09 19:00 https://…» — (время, ссылка, свой текст).

    Время без года: год берём текущий, а если дата уже прошла — следующий.
    Так «31.12 22:00», отправленное в январе, не окажется в прошлом.
    """
    import re

    url = re.search(r'https?://\S+', text or '')
    when = re.search(r'(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s+(\d{1,2})[:.](\d{2})',
                     text or '')
    if not url or not when:
        return None, '', ''
    day, month, year, hour, minute = when.groups()
    moment = datetime.now(stats.MSK) if now is None else datetime.fromtimestamp(now, stats.MSK)
    year = int(year) + (2000 if year and len(year) == 2 else 0) if year else moment.year
    try:
        at = datetime(year, int(month), int(day), int(hour), int(minute), tzinfo=stats.MSK)
    except ValueError:
        return None, '', ''
    if at < moment - timedelta(hours=1) and not when.group(3):
        at = at.replace(year=year + 1)
    own = (text or '').replace(url.group(0), '').replace(when.group(0), '').strip()
    return at.timestamp(), url.group(0), own


def when_text(at: float) -> str:
    u"""«сегодня в 19:00» — по-московски, как привыкла команда."""
    moment = datetime.fromtimestamp(at, stats.MSK)
    today = datetime.now(stats.MSK).date()
    if moment.date() == today:
        day = u'сегодня'
    elif moment.date() == today + timedelta(days=1):
        day = u'завтра'
    else:
        day = moment.strftime('%d.%m')
    return u'%s в %s (МСК)' % (day, moment.strftime('%H:%M'))


def announce_text(live: dict, left: int | None = None) -> str:
    u"""Текст анонса или напоминания."""
    own = (live.get('text') or '').strip()
    if left == 0:
        head = texts.LIVE_NOW
    elif left is not None:
        head = texts.LIVE_SOON.format(left=texts.LIVE_LEFT.get(left, u'скоро'))
    else:
        head = texts.LIVE_ANNOUNCE.format(when=when_text(live['at']))
    return head + ((u'\n\n' + own) if own else u'')


async def _spread(bot, live: dict, left: int | None) -> dict:
    u"""Разослать анонс или напоминание всем, кому бот ещё может писать."""
    text = announce_text(live, left)
    keys = keyboards.live(live['url'])
    sent = gone = failed = 0
    after = 0
    while True:
        people = db.broadcast_targets(after, 200)
        if not people:
            break
        for user_id in people:
            after = user_id
            try:
                await delivery._guard(bot.send_message(user_id, text, reply_markup=keys))
            except delivery.Gone:
                db.mark_blocked(user_id)
                gone += 1
                continue
            except Exception as err:
                log.warning(u'эфир: %s не получил: %s', user_id, err)
                failed += 1
                continue
            sent += 1
            await asyncio.sleep(1.0 / PER_SECOND)
    log.info(u'эфир %s: ушло %d, закрыли бота %d, сбоев %d',
             live['id'], sent, gone, failed)
    return {'sent': sent, 'gone': gone, 'failed': failed}


async def announce(bot, live_id: int) -> dict:
    u"""Объявить об эфире — сразу после подтверждения."""
    live = db.live(live_id)
    if not live or live['status'] != 'ready':
        return {}
    db.live_status(live_id, 'going')
    result = await _spread(bot, live, None)
    await _report(bot, live, texts.LIVE_SENT, result)
    return result


async def _report(bot, live: dict, head: str, result: dict) -> None:
    text = head + texts.LIVE_REPORT.format(**result)
    for chat in dict.fromkeys((live['author'],) + config.report_recipients()):
        try:
            await bot.send_message(chat, text)
        except Exception:
            continue


async def loop(bot) -> None:
    u"""Раз в полминуты — не пора ли напомнить об эфире."""
    log.info(u'напоминания об эфирах включены')
    while True:
        await asyncio.sleep(EVERY)
        try:
            await tick(bot)
        except Exception:
            log.exception(u'сбой напоминаний об эфире')


async def tick(bot, now: float | None = None) -> int:
    u"""Разослать созревшие напоминания. Возвращает, сколько их было."""
    now = time.time() if now is None else now
    done = 0
    for live in db.lives_going():
        for left in REMINDERS:
            if db.live_reminded(live['id'], left):
                continue
            # Напоминание за час не шлём, если до эфира уже меньше: человек
            # получил бы «через час» за пять минут до начала.
            if now < live['at'] - left:
                continue
            if now > live['at'] + 900:          # эфир давно начался — молчим
                db.live_remind(live['id'], left)
                continue
            db.live_remind(live['id'], left)
            result = await _spread(bot, live, left)
            await _report(bot, live, texts.LIVE_REMINDED.format(
                left=texts.LIVE_LEFT.get(left, u'скоро')), result)
            done += 1
        if now > live['at'] + 900:
            db.live_status(live['id'], 'done')
    return done
