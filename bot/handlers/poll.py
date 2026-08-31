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

    if not user or user.get('poll') != poll:
        await call.answer(u'Этот вопрос уже закрыт')
        return

    branches = funnel.POLL_BRANCHES[poll]
    db.save_answer(user_id, poll, answer)
    db.set_poll(user_id, None)
    db.drop_chains(user_id, tuple(branches.values()))

    await call.answer(texts.POLL_YES if answer == 'yes' else texts.POLL_NO)
    chosen = texts.POLL_YES if answer == 'yes' else texts.POLL_NO
    try:
        await call.message.edit_text(u'%s\n\n<b>%s</b>'
                                     % (texts.POLL_QUESTIONS[poll], chosen))
    except Exception:
        log.debug(u'не переписали опросник %s у %s', poll, user_id)

    scheduler.start_chain(user_id, branches[answer])
    log.info(u'%s ответил «%s» на %s', user_id, answer, poll)
