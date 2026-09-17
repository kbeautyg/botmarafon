# -*- coding: utf-8 -*-
u"""Дожим: «заходи на марафон» тем, кто не ответил на вопрос дня.

AleX 14.09.2026: «кто зашёл в бота и не ответил да или нет (на второй день
не перешёл сам), то через 5 часов после его первого входа (нажатие на
ссылку «Старт марафона») прислать ему текст в боте оповещением: «Мой друг,
заходи на марафон. Я уже прислал первую полезную информацию»».

Первый день считается от запуска марафона — так и просил AleX. Второй и
третий (17.09.2026) — от того момента, когда человеку ушёл вопрос: с этого
дня без ответа следующий день не приходит, и молчун после второго или
третьего дня иначе остался бы стоять навсегда.

Кому: пять часов прошло, на вопрос не ответил, бота не закрывал, дожима по
этому вопросу ещё не было. Один раз на прогон марафона: «пройти заново»
сбрасывает отметки.

Задним числом не пишем. При первом запуске отмечается момент AFTER назад:
кого спросили раньше него, дожим не получит, — иначе бот разом написал бы
всем прежним молчунам.
"""
import asyncio
import logging
import time

from . import db, delivery, texts

log = logging.getLogger(__name__)

AFTER = 5 * 3600          # через сколько часов молчания напоминать
EVERY = 60                # как часто проверять
SINCE = 'nudge:since'     # с какого запуска марафона дожим действует
SINCE_POLL = 'nudge:since:%s'   # с какого вопроса дожим действует
LATER_POLLS = ('day2', 'day3')  # вопросы после второго и третьего дня


def _since(key: str, now: float) -> float:
    u"""С какого момента действует дожим; при первом вызове — ставится."""
    known = db.get_content(key)
    if known:
        return float(known[1])
    start = now - AFTER
    db.put_content(key, 'nudge', repr(start))
    return start


def since(now: float) -> float:
    u"""С какого запуска марафона действует дожим первого дня."""
    return _since(SINCE, now)


def since_poll(poll: str, now: float) -> float:
    u"""С какого вопроса действует дожим второго или третьего дня."""
    return _since(SINCE_POLL % poll, now)


async def _push(bot, user_id: int, text: str, mark, label: str) -> bool:
    u"""Отправить дожим и отметить его. False — не ушёл."""
    try:
        await delivery._guard(bot.send_message(user_id, text))
    except delivery.Gone:
        db.mark_blocked(user_id)
        mark()
        return False
    except Exception as err:          # сбой связи — попробуем через минуту
        log.warning(u'дожим (%s) %s не ушёл: %s', label, user_id, err)
        return False
    mark()
    log.info(u'дожим «заходи на марафон» (%s) ушёл %s', label, user_id)
    return True


async def run_once(bot, now: float | None = None) -> int:
    u"""Разослать созревшие дожимы. Возвращает, скольким ушло."""
    now = time.time() if now is None else now
    sent = 0
    for uid in db.nudge_candidates(since(now), now - AFTER):
        sent += await _push(bot, uid, texts.NUDGE_DAY1,
                            lambda uid=uid: db.mark_nudged(uid), u'день 1')
    for poll in LATER_POLLS:
        for uid in db.poll_nudge_candidates(poll, since_poll(poll, now), now - AFTER):
            sent += await _push(bot, uid, texts.NUDGE_POLL[poll],
                                lambda uid=uid, poll=poll: db.mark_poll_nudged(uid, poll),
                                poll)
    return sent


async def loop(bot) -> None:
    u"""Раз в минуту — созревшие дожимы. Сбой одного круга не останавливает."""
    while True:
        try:
            await run_once(bot)
        except Exception:
            log.exception(u'сбой дожима')
        await asyncio.sleep(EVERY)
