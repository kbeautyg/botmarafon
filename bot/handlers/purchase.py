# -*- coding: utf-8 -*-
u"""Кнопки покупки и заявка менеджеру.

Пункт «Важные моменты 1» из ТЗ: как только человек купил, заявка должна
упасть менеджеру, чтобы с человеком связались сразу.

Кому уходит заявка — config.purchase_recipients(): чат PURCHASE_CHAT_ID и
лично Павлу и AleX (Sharp 13.09.2026: «прикрепи Павла и Алекса» — до этого
заявки видела только личка Sharp).

Одна кнопка, нажатая несколько раз подряд, — одна заявка (AleX 13.09.2026:
«Наталья четыре раза ткнула одно и то же, а в отчёте четыре человека»).
Каждое нажатие по-прежнему пишется в базу — так видно, сколько раз
человек жал, — но менеджерам повтор за сутки второй раз не уходит.
"""
import html
import logging
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery

from .. import config, contact, db, delivery, keyboards, texts

log = logging.getLogger(__name__)
router = Router(name='purchase')

PRODUCTS = {
    'gym': texts.OFFER_GYM,
    'course': texts.OFFER_COURSE,
}

# Та же кнопка ещё раз в течение суток — та же заявка. Позже — человек
# вернулся к покупке, и это снова сигнал менеджеру.
REPEAT_WINDOW = 24 * 3600


def _who(user):
    u"""Как показать человека менеджеру: имя ссылкой на профиль, ник и id."""
    return contact.line(user.id, user.full_name, user.username, bold=True)


async def _deliver(bot, number, note: str, user_id: int) -> None:
    u"""Заявка — всем получателям. Не дошла ни до кого — шуметь админам.

    Заявка уже в базе и не пропадёт, но если её не увидел ни один человек,
    менеджер не позвонит — значит, молчать нельзя. Дошла хотя бы до одного
    из получателей — человек о покупке знает, это не авария.
    """
    got, failed = [], []
    for chat in config.purchase_recipients():
        try:
            sent = await delivery.note(bot, chat, note,
                                       keyboards.ban_ask(user_id, chat),
                                       keyboards.ban_ask(user_id))
            got.append(chat)
            # реплай на заявку — сообщение покупателю (handlers/support.py)
            if sent is not None:
                db.link_care(chat, sent.message_id, user_id)
        except Exception as err:
            failed.append(u'%s: %s' % (chat, err))
            log.warning(u'заявка %s не ушла в %s: %s', number, chat, err)
    if failed and not got:
        await delivery.alert_admins(
            bot=bot,
            text=u'⚠️ Заявка №%s не дошла ни до кого: %s\n\n%s'
                 % (number, html.escape(u'; '.join(failed))[:1000], note))


async def _reply(call: CallbackQuery, toast: str, text: str, markup=None) -> None:
    u"""Ответ человеку. Его сбой — не повод терять заявку: нажатие,
    обработанное с опозданием (бот перезапускался на выкладке), Telegram
    уже не даёт подтвердить — «query is too old»."""
    try:
        await call.answer(toast)
    except Exception as err:
        log.debug(u'нажатие покупки у %s не подтвердили: %s', call.from_user.id, err)
    try:
        await call.message.answer(text, reply_markup=markup)
    except Exception as err:
        log.warning(u'не ответили %s на покупку: %s', call.from_user.id, err)


@router.callback_query(F.data.startswith('buy:'))
async def on_buy(call: CallbackQuery):
    product = call.data.split(':', 1)[1]
    title = PRODUCTS.get(product)
    if not title:
        await call.answer()
        return

    user_id = call.from_user.id
    # Есть ссылка на оплату — человек уходит туда сразу, заявка команде всё равно
    # (Павел 17.09.2026). Нет — как раньше: «менеджер свяжется».
    pay_url = config.PAY_URLS.get(product) or ''
    answer = (texts.OFFER_PAY, keyboards.pay(pay_url)) if pay_url else None
    earlier = db.purchase_presses(user_id, product)
    number = db.add_purchase(user_id, product)
    db.unblock(user_id)                        # нажал кнопку — бот у него открыт

    if earlier and time.time() - earlier[-1]['at'] < REPEAT_WINDOW:
        log.info(u'%s снова нажал «%s» (%d-й раз) — заявка та же, менеджерам не шлём',
                 user_id, product, len(earlier) + 1)
        if answer:
            await _reply(call, u'Открываю оплату', *answer)
        else:
            await _reply(call, u'Заявка уже принята', texts.OFFER_ALREADY)
        return

    # Сначала заявка менеджерам, потом ответ человеку: сбой ответа не должен
    # оставить заявку только в базе (ревью 13.09.2026).
    again = (u'\n\nПовторная заявка: человек вернулся к кнопке, нажатие №%d' % (len(earlier) + 1)
             if earlier else u'')
    paid = u'\n%s' % texts.PAY_NOTE if pay_url else u''
    note = u'🛒 <b>Заявка №%s</b>\n%s\n\nВыбор: <b>%s</b>%s%s\n\n%s' % (
        number, _who(call.from_user), title, paid, again, texts.REPLY_HINT)
    await _deliver(call.bot, number, note, user_id)
    if answer:
        await _reply(call, u'Открываю оплату', *answer)
    else:
        await _reply(call, u'Заявка принята', texts.OFFER_DONE)
