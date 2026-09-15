# -*- coding: utf-8 -*-
u"""Дожим: «заходи на марафон» тем, кто не ответил на первый вопрос.

AleX 14.09.2026: «кто зашёл в бота и не ответил да или нет (на второй день
не перешёл сам), то через 5 часов после его первого входа (нажатие на
ссылку «Старт марафона») прислать ему текст в боте оповещением: «Мой друг,
заходи на марафон. Я уже прислал первую полезную информацию»».

Кому: запустил марафон не меньше AFTER назад, первый день ему уже ушёл, на
вопрос после первого дня не ответил, бота не закрывал, дожим ещё не
получал. Один раз на прогон марафона: «пройти заново» сбрасывает отметку.

Задним числом не пишем. При первом запуске отмечается момент AFTER назад:
кто запустил марафон раньше него, дожим не получит, — иначе бот разом
написал бы всем прежним молчунам.
"""
import asyncio
import logging
import time

from . import db, delivery, texts

log = logging.getLogger(__name__)

AFTER = 5 * 3600          # через сколько после запуска напоминать
EVERY = 60                # как часто проверять
SINCE = 'nudge:since'     # с какого запуска марафона дожим действует


def since(now: float) -> float:
    u"""С какого момента запуска действует дожим; при первом вызове — ставится."""
    known = db.get_content(SINCE)
    if known:
        return float(known[1])
    start = now - AFTER
    db.put_content(SINCE, 'nudge', repr(start))
    return start


async def run_once(bot, now: float | None = None) -> int:
    u"""Разослать созревшие дожимы. Возвращает, скольким ушло."""
    now = time.time() if now is None else now
    sent = 0
    for uid in db.nudge_candidates(since(now), now - AFTER):
        try:
            await delivery._guard(bot.send_message(uid, texts.NUDGE_DAY1))
        except delivery.Gone:
            db.mark_blocked(uid)
            db.mark_nudged(uid)
            continue
        except Exception as err:          # сбой связи — попробуем через минуту
            log.warning(u'дожим %s не ушёл: %s', uid, err)
            continue
        db.mark_nudged(uid)
        sent += 1
        log.info(u'дожим «заходи на марафон» ушёл %s', uid)
    return sent


async def loop(bot) -> None:
    u"""Раз в минуту — созревшие дожимы. Сбой одного круга не останавливает."""
    while True:
        try:
            await run_once(bot)
        except Exception:
            log.exception(u'сбой дожима')
        await asyncio.sleep(EVERY)
