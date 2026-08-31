# -*- coding: utf-8 -*-
u"""Планировщик: кто и когда получает следующий шаг.

Раз в секунду заглядывает в очередь, берёт всё созревшее,
отправляет и ставит следующий шаг. Ничего не держит в памяти — состояние
целиком в базе, поэтому перезапуск бота посреди чьей-то паузы не рвёт
воронку: после старта очередь просто разбирается дальше.
"""
import asyncio
import logging
import time

from aiogram import Bot

from . import config, db, delivery, funnel

log = logging.getLogger(__name__)

MAX_TRIES = 3
RETRY_PAUSE = 60

# Насколько шаг может опоздать, чтобы следующий всё ещё считался от плана.
GRACE = 5


def schedule(user_id: int, chain: str, pos: int, delay: float,
             base: float | None = None) -> None:
    u"""Поставить шаг в очередь с учётом ускорения (режим /test).

    base — от какого момента отсчитывать паузу. Для продолжения цепочки это
    намеченное время предыдущего шага, а не «сейчас»: иначе к каждой паузе
    приклеивается время отправки и ожидание тика, и восемь отзывов «каждые
    две секунды» расползаются на минуту.
    """
    speed = db.get_speed(user_id)
    now = time.time()
    # Мелкое опоздание (ожидание тика, время отправки) не копим — считаем от
    # намеченного. А если бот пролежал дольше GRACE, от плана уже не пляшем:
    # иначе весь накопленный хвост посыплется человеку одной лавиной.
    if base is None or now - base > GRACE:
        base = now
    db.add_job(user_id, chain, pos, base + delay * speed)


def start_chain(user_id: int, chain: str) -> None:
    u"""Запустить цепочку с её первого шага."""
    first = funnel.CHAINS[chain].steps[0]
    schedule(user_id, chain, 0, first.delay)


def _plan_next(user_id: int, chain: str, pos: int, step: funnel.Step,
               base: float | None = None) -> None:
    u"""Что поставить после выполненного шага.

    У опросника продолжения нет: дальше ведёт ответ человека. Но если он
    не ответит совсем, воронка встанет навсегда — поэтому на ветку «нет»
    заранее ставится отложенное добивание. Ответ его снимет.
    """
    if step.kind == 'poll':
        if config.POLL_FALLBACK_HOURS > 0:
            fallback = funnel.POLL_BRANCHES[step.ref]['no']
            schedule(user_id, fallback, 0, config.POLL_FALLBACK_HOURS * 3600)
        return

    following = funnel.next_after(chain, pos)
    if following:
        schedule(user_id, following[0], following[1], following[2], base=base)


async def run_job(bot: Bot, job: dict) -> None:
    u"""Один шаг: отправить и запланировать следующий."""
    step = funnel.step_at(job['chain'], job['pos'])
    if step is None:
        log.error(u'шаг %s#%s пропал из сценария', job['chain'], job['pos'])
        db.drop_job(job['id'])
        return

    try:
        await delivery.perform(bot, job['user_id'], step)
    except delivery.Gone:
        # Человек закрыл бота: снимаем всё, что ему было запланировано,
        # иначе очередь будет биться о него до конца воронки.
        log.info(u'%s закрыл бота — снимаем его очередь', job['user_id'])
        db.drop_job(job['id'])
        db.drop_chains(job['user_id'], tuple(funnel.CHAINS))
        return
    except Exception as err:
        if job['tries'] + 1 >= MAX_TRIES:
            log.exception(u'шаг %s#%s для %s провален окончательно: %s',
                          job['chain'], job['pos'], job['user_id'], err)
            db.drop_job(job['id'])
            await delivery.alert_admins(
                bot, u'⚠️ Не отправили шаг %s#%s человеку %s: %s'
                     % (job['chain'], job['pos'], job['user_id'], err))
            return
        log.warning(u'шаг %s#%s не ушёл (%s), повторим через минуту',
                    job['chain'], job['pos'], err)
        db.retry_job(job['id'], time.time() + RETRY_PAUSE)
        return

    db.drop_job(job['id'])
    log.info(u'%s ← %s#%s (%s %s)', job['user_id'], job['chain'], job['pos'],
             step.kind, step.ref if step.ref is not None else '')
    _plan_next(job['user_id'], job['chain'], job['pos'], step, base=job['run_at'])


async def tick(bot: Bot) -> int:
    u"""Разобрать созревшие шаги. Возвращает, сколько выполнено."""
    jobs = db.due_jobs(config.JOBS_PER_TICK)
    for job in jobs:
        await run_job(bot, job)
    return len(jobs)


async def loop(bot: Bot) -> None:
    u"""Вечный цикл планировщика. Падение одного шага не роняет остальные."""
    log.info(u'планировщик запущен, тик %s сек', config.TICK_SECONDS)
    while True:
        try:
            await tick(bot)
        except Exception:
            log.exception(u'сбой тика планировщика')
        await asyncio.sleep(config.TICK_SECONDS)
