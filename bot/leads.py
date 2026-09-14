# -*- coding: utf-8 -*-
u"""Марафон людям, пришедшим в бота по заявке с сайта.

До 14.09.2026 такой человек получал только «заявка принята, менеджер
напишет» (решение Павла 09.09.2026). AleX 14.09.2026: «бот видит тех, кто
вбил заявку и перешёл, но ничего не делает — пусть сразу запускается
марафон, первым сообщением и первым кружком». Sharp подтвердил: новым —
сразу (handlers/start.py), а тем, кто пришёл раньше, — тоже.

Прежним людям бот не пишет без спроса. После выкладки он один раз
присылает админам список, кому уйдёт, с кнопкой; отправка — только по
нажатию. Писать им можно: каждый сам нажимал «Запустить» в боте, а
Telegram разрешает боту отвечать таким людям. Лимиты (десятки сообщений в
секунду) при таком числе людей не при чём, бота за это не ограничат.

Тем, кто оставил заявку на сайте, но в бота не заходил, бот написать не
может вовсе: первым писать людям ботам Telegram запрещает.
"""
import asyncio
import logging
from datetime import datetime

from . import config, contact, db, delivery, keyboards, scheduler, stats, texts

log = logging.getLogger(__name__)

NOTICE = 'notice:leads_backfill'     # список админам уже доставлен
PAUSE = 0.1                          # между людьми: бережём лимиты Telegram
MAX_LIST = 40                        # строк в списке: иначе не влезет в сообщение


def pending() -> list[dict]:
    u"""Пришли по заявке, марафон не запускали, бота не закрывали. Без команды."""
    team = set(config.ADMIN_IDS) | set(config.STATS_IDS)
    return [p for p in db.waiting_leads() if p['user_id'] not in team]


def report(people: list[dict]) -> str:
    u"""Список для админа: кто получит марафон по кнопке."""
    if not people:
        return texts.LEADS_NONE
    lines = [texts.LEADS_HEAD % len(people)]
    for n, p in enumerate(people[:MAX_LIST], 1):
        when = datetime.fromtimestamp(p['started_at'], stats.MSK).strftime('%d.%m')
        no = u' · заявка №%d' % p['lead_no'] if p.get('lead_no') else u''
        lines.append(u'%d. %s · %s%s' % (
            n, contact.line(p['user_id'], p['first_name'], p['username']), when, no))
    if len(people) > MAX_LIST:
        lines.append(u'…и ещё %d' % (len(people) - MAX_LIST))
    lines += [u'', texts.LEADS_HINT]
    return u'\n'.join(lines)


async def offer_backfill(bot) -> None:
    u"""Один раз после выкладки: список админам с кнопкой. Нечего слать — молчим.

    Отметка ставится, только когда список дошёл хотя бы до одного админа:
    не дошёл — попробуем при следующем запуске, а не потеряем молча.
    """
    if db.get_content(NOTICE):
        return
    people = pending()
    if not people:
        db.put_content(NOTICE, 'notice', 'пусто')
        return
    delivered = False
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, report(people),
                                   reply_markup=keyboards.leads_launch(len(people)))
            delivered = True
        except Exception as err:
            log.warning(u'список людей с заявки не ушёл админу %s: %s', admin, err)
    if delivered:
        db.put_content(NOTICE, 'notice', str(len(people)))


async def launch_all(bot) -> dict:
    u"""Каждому: короткое сообщение, затем марафон с первого кружка.

    Сообщение первым: закрывший бота узнаётся на нём, и марафон ему не
    ставится. Повторное нажатие кнопки безопасно — запущенным марафон уже
    не ставится, список пуст.
    """
    done = closed = failed = 0
    for p in pending():
        uid = p['user_id']
        try:
            await delivery._guard(bot.send_message(uid, texts.LEAD_GIFT,
                                                   reply_markup=keyboards.care()))
        except delivery.Gone:
            db.mark_blocked(uid)
            closed += 1
            continue
        except Exception as err:
            log.warning(u'марафон человеку с заявки %s не ушёл: %s', uid, err)
            failed += 1
            continue
        if db.mark_launched(uid):
            scheduler.start_chain(uid, 'launch')
            db.count_launch(uid)
            done += 1
            log.info(u'марафон запущен человеку с заявки %s', uid)
        if PAUSE:
            await asyncio.sleep(PAUSE)
    return {'done': done, 'closed': closed, 'failed': failed}
