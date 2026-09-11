# -*- coding: utf-8 -*-
u"""Опросники «посмотрел день?» и ветвление после них."""
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import db, funnel, scheduler, texts

log = logging.getLogger(__name__)
router = Router(name='poll')


@router.callback_query(F.data.startswith('poll:'))
async def on_answer(call: CallbackQuery):
    u"""Ответ на опросник запускает свою ветку.

    Два обязательных действия перед запуском. Первое — снять отложенное
    добивание по ветке «нет»: оно стоит в очереди с момента вопроса, и без
    снятия человек получил бы обе ветки. Второе — обнулить ожидание, чтобы
    повторное нажатие на ту же кнопку не запустило ветку второй раз.
    """
    _, poll, answer = call.data.split(':')
    user_id = call.from_user.id
    user = db.get_user(user_id)

    # Отвеченный вопрос закрыт, даже если его снова открыл повторный вопрос,
    # ушедший в ту же секунду: вторая ветка задвоила бы марафон (ревью 11.09).
    if not user or user.get('poll') != poll or db.answered(user_id, poll):
        await call.answer(u'Этот вопрос уже закрыт')
        return

    branches = funnel.POLL_BRANCHES[poll]
    db.save_answer(user_id, poll, answer)
    db.unblock(user_id)                        # нажал кнопку — бот у него открыт
    db.set_poll(user_id, None)
    db.drop_chains(user_id, tuple(branches.values()))
    # Ветка встаёт в очередь сразу, до любого обращения к Telegram. Раньше
    # она шла последней: нажатие, обработанное с опозданием (бот
    # перезапускался на выкладке), Telegram не даёт подтвердить — «query is
    # too old», — обработчик падал, и человек навсегда оставался без
    # следующего дня, без единого сигнала админу (ревью 11.09.2026).
    scheduler.start_chain(user_id, branches[answer])
    log.info(u'%s ответил «%s» на %s', user_id, answer, poll)

    chosen = texts.POLL_YES if answer == 'yes' else texts.POLL_NO
    try:
        await call.answer(chosen)
    except Exception as err:                   # запоздалое нажатие — не беда
        log.debug(u'нажатие %s у %s не подтвердили: %s', poll, user_id, err)
    try:
        await call.message.edit_text(u'%s\n\n<b>%s</b>'
                                     % (texts.POLL_QUESTIONS[poll], chosen))
    except Exception:
        log.debug(u'не переписали опросник %s у %s', poll, user_id)
