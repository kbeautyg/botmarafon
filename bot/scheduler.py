# -*- coding: utf-8 -*-
u"""Планировщик: кто и когда получает следующий шаг.

Раз в секунду заглядывает в очередь, берёт всё созревшее,
отправляет и ставит следующий шаг. Ничего не держит в памяти — состояние
целиком в базе, поэтому перезапуск бота посреди чьей-то паузы не рвёт
воронку: после старта очередь просто разбирается дальше.
"""
import asyncio
import html
import logging
import time

from aiogram import Bot

from . import config, db, delivery, funnel

log = logging.getLogger(__name__)

MAX_TRIES = 3
RETRY_PAUSE = 60

# Сбой связи с Telegram (delivery.is_transient) — не вина шага. Три попытки
# за две минуты роняли бы при любом сбое дольше двух минут все созревшие
# шаги у всех людей разом (ревью 11.09.2026). Такие сбои ждём с растущей
# паузой, не длиннее RETRY_CAP, — около трёх с половиной часов; и даже потом
# шаг снимается, только если админы об этом узнали.
TRANSIENT_TRIES = 12
RETRY_CAP = 30 * 60

# Ветка ответа → её вопрос: day1_no → day1. Нужна, чтобы закрыть вопрос,
# когда ветка «нет» пошла сама, по таймеру.
BRANCH_POLL = {chain: poll for poll, branches in funnel.POLL_BRANCHES.items()
               for chain in branches.values()}

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
        # ответ пришёл, пока вопрос уходил, — ветка уже идёт, добивание лишнее
        if config.POLL_FALLBACK_HOURS > 0 and not db.answered(user_id, step.ref):
            fallback = funnel.POLL_BRANCHES[step.ref]['no']
            schedule(user_id, fallback, 0, config.POLL_FALLBACK_HOURS * 3600)
        return

    following = funnel.next_after(chain, pos)
    if following:
        schedule(user_id, following[0], following[1], following[2], base=base)


def requeue_poll(user_id: int, poll: str) -> bool:
    u"""Поставить заново вопрос «посмотрел?» — вернувшемуся к открытому вопросу.

    Шаг-вопрос цепочки after_dayN уходит сразу; дальше всё как обычно: вопрос
    снова открыт, ветка «нет» встаёт на таймер. False — такого вопроса нет.
    """
    chain = 'after_' + poll
    steps = funnel.CHAINS[chain].steps if chain in funnel.CHAINS else ()
    pos = next((i for i, step in enumerate(steps) if step.kind == 'poll'), None)
    if pos is None:
        return False
    db.add_job(user_id, chain, pos, time.time())
    return True


def _plain(err: Exception) -> str:
    u"""Текст ошибки для сообщения с разметкой: в нём бывает целая HTML-страница
    шлюза — без экранирования Telegram отверг бы само предупреждение."""
    return html.escape(str(err))[:1000]


def _retry_pause(err: Exception, tries: int) -> float:
    u"""Пауза перед повтором при сбое связи: 1, 2, 4… минуты, не длиннее
    RETRY_CAP; назвал Telegram срок сам («подождите N секунд») — не меньше."""
    pause = min(RETRY_PAUSE * 2 ** tries, RETRY_CAP)
    return max(pause, getattr(err, 'retry_after', 0) or 0)


def _close_poll(job: dict) -> str | None:
    u"""Ветка «нет» пошла сама, по таймеру: человек так и не ответил.

    Вопрос закрываем до отправки. Иначе старая кнопка под ним запустила бы
    ветку второй раз — тот же день и весь остаток воронки параллельно, — а
    статистика вечно числила бы его «не ответившим» (ревью 11.09.2026).
    Возвращает закрытый вопрос, чтобы открыть его обратно, если шаг не ушёл.
    """
    poll = BRANCH_POLL.get(job['chain'])
    if not poll or job['pos'] != 0:
        return None
    user = db.get_user(job['user_id'])
    if not user or user.get('poll') != poll:
        return None
    db.set_poll(job['user_id'], None)
    return poll


def _reopen_poll(job: dict, poll: str | None) -> None:
    u"""Шаг не ушёл — вопрос снова открыт. Кнопка под ним остаётся способом
    продолжить: при повторе через минуту и когда закрывший бота вернётся."""
    if poll:
        db.set_poll(job['user_id'], poll)


async def run_job(bot: Bot, job: dict) -> None:
    u"""Один шаг: отправить и запланировать следующий."""
    step = funnel.step_at(job['chain'], job['pos'])
    if step is None:
        log.error(u'шаг %s#%s пропал из сценария', job['chain'], job['pos'])
        db.drop_job(job['id'])
        return

    if step.kind == 'poll' and db.answered(job['user_id'], step.ref):
        # На вопрос уже ответили — старой кнопкой, пока вопрос ждал в очереди
        # (/start после блокировки ставит его заново). Второй раз не спрашиваем:
        # иначе пошли бы две ветки разом (ревью 11.09.2026).
        log.info(u'%s уже ответил на %s — повторный вопрос снят', job['user_id'], step.ref)
        db.drop_job(job['id'])
        return

    closed = _close_poll(job)

    try:
        await delivery.perform(bot, job['user_id'], step)
    except delivery.Gone:
        # Человек закрыл бота: снимаем всё, что ему было запланировано,
        # иначе очередь будет биться о него до конца воронки.
        log.info(u'%s закрыл бота — снимаем его очередь', job['user_id'])
        _reopen_poll(job, closed)
        db.drop_job(job['id'])
        db.drop_chains(job['user_id'], tuple(funnel.CHAINS))
        _for_stats(db.mark_blocked, job['user_id'])
        return
    except Exception as err:
        _reopen_poll(job, closed)
        transient = delivery.is_transient(err)
        if job['tries'] + 1 >= (TRANSIENT_TRIES if transient else MAX_TRIES):
            told = await delivery.alert_admins(
                bot, u'⚠️ Не отправили шаг %s#%s человеку %s: %s'
                     % (job['chain'], job['pos'], job['user_id'], _plain(err)))
            if transient and not told:
                # Telegram всё ещё лежит, и админам не сказать: снять шаг сейчас
                # значит потерять человека молча. Ждём дальше (ревью 11.09.2026).
                log.warning(u'шаг %s#%s для %s: связи нет и после %d попыток — ждём',
                            job['chain'], job['pos'], job['user_id'], job['tries'] + 1)
                db.retry_job(job['id'], time.time() + RETRY_CAP)
                return
            log.exception(u'шаг %s#%s для %s провален окончательно: %s',
                          job['chain'], job['pos'], job['user_id'], err)
            db.drop_job(job['id'])
            return
        pause = _retry_pause(err, job['tries']) if transient else RETRY_PAUSE
        log.warning(u'шаг %s#%s не ушёл (%s), повторим через %d с',
                    job['chain'], job['pos'], err, pause)
        db.retry_job(job['id'], time.time() + pause)
        return

    db.drop_job(job['id'])
    log.info(u'%s ← %s#%s (%s %s)', job['user_id'], job['chain'], job['pos'],
             step.kind, step.ref if step.ref is not None else '')
    _plan_next(job['user_id'], job['chain'], job['pos'], step, base=job['run_at'])
    # Для статистики — строго ПОСЛЕ планирования следующего шага: сбой записи
    # не должен оставить человека без марафона (ревью 11.09.2026). Кружки и
    # отзывы не пишем — их полтора десятка, о пути они ничего не говорят.
    if step.kind in ('day', 'poll', 'offer'):
        _for_stats(db.log_event, job['user_id'], step.kind, step.ref)
    # дошло — значит, бот у человека открыт, даже если раньше он его закрывал
    _for_stats(db.unblock, job['user_id'])


def _for_stats(write, *args) -> None:
    u"""Запись только для статистики: её сбой воронку не останавливает."""
    try:
        write(*args)
    except Exception as err:
        log.warning(u'запись для статистики не удалась (%s): %s', write.__name__, err)


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
