# -*- coding: utf-8 -*-
u"""Кнопки покупки и заявка менеджеру.

Пункт «Важные моменты 1» из ТЗ: как только человек купил, заявка должна
упасть в отдельный чат покупок, чтобы менеджер связался сразу.
"""
import html
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import config, db, delivery, texts

log = logging.getLogger(__name__)
router = Router(name='purchase')

PRODUCTS = {
    'gym': texts.OFFER_GYM,
    'course': texts.OFFER_COURSE,
}


def _who(user):
    u"""Как показать человека менеджеру: имя, ник и id для поиска."""
    name = html.escape(user.full_name or u'без имени')
    handle = u'@%s' % user.username if user.username else u'без ника'
    return u'<b>%s</b> · %s · <code>%s</code>' % (name, handle, user.id)


@router.callback_query(F.data.startswith('buy:'))
async def on_buy(call: CallbackQuery):
    product = call.data.split(':', 1)[1]
    title = PRODUCTS.get(product)
    if not title:
        await call.answer()
        return

    number = db.add_purchase(call.from_user.id, product)
    await call.answer(u'Заявка принята')
    await call.message.answer(texts.OFFER_DONE)

    note = (u'🛒 <b>Заявка №%s</b>\n%s\n\nВыбор: <b>%s</b>'
            % (number, _who(call.from_user), title))
    try:
        await call.bot.send_message(config.PURCHASE_CHAT_ID, note)
    except Exception as err:
        # Заявка уже в базе, так что не пропадёт, но менеджер её не увидит —
        # значит надо шуметь, а не глотать ошибку.
        log.exception(u'заявка %s не ушла в чат покупок: %s', number, err)
        await delivery.alert_admins(
            bot=call.bot,
            text=u'⚠️ Заявка №%s не дошла до чата покупок: %s\n\n%s'
                 % (number, err, note))
