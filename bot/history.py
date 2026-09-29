# -*- coding: utf-8 -*-
u"""Записи дней в старых переписках — чтобы закрыть и их.

До 29.09.2026 бот не запоминал, в каком сообщении ушла запись дня, а без
номера сообщения закрыть запись нельзя (bot/closing.py). Bot API историю
читать не умеет, но бот может зайти в Telegram по протоколу клиента
(MTProto, Telethon — так же он заливает записи, tools/upload_days_bot.py)
и прочитать свои сообщения по номерам. В личных переписках у бота одна
нумерация на всех, поэтому достаточно пройти номера подряд и отобрать свои
сообщения с записями дней. Узнаём их по подписи: первые слова текста дня
не менялись с 31.08.2026 (проверено на истории бота 29.09.2026).

Работает один раз (отметка history:days в content), в фоне, ничего не
отправляет — только читает. Нужны TG_API_ID и TG_API_HASH: пара любого
клиента Telegram. Без них поиск не запускается, старые записи остаются
открытыми, а баннер приходит всё равно.
"""
import asyncio
import html
import logging
import re

from . import config, db, delivery, texts

log = logging.getLogger(__name__)

DONE = 'history:days'
BATCH = 100          # столько номеров Telegram отдаёт за один запрос
EMPTY_RUNS = 30      # столько пустых пачек подряд за последним известным — конец истории
PAUSE = 0.1

# Первые слова подписей записей дней (texts.DAY_TEXTS) — по ним запись и
# узнаётся. Тест следит, чтобы тексты дней с них и начинались.
MARKS = {
    1: u'Первый день марафона самый длинный',
    2: u'Сегодня второй день нашего марафона',
    3: u'Третий день — это сфера ЗДОРОВЬЕ',
    4: u'Сферу ФИНАНСОВ мы разберём',
}
# Досланная запись (/resend): своя подпись, texts.DAY_RESEND.
RESEND = re.compile(u'^Запись дня ([1-4]) готова')


def day_of(text: str | None) -> int | None:
    u"""Какой день в подписи. None — это не запись дня."""
    text = u' '.join((text or u'').split())
    resend = RESEND.match(text)
    if resend:
        return int(resend.group(1))
    for day, mark in MARKS.items():
        # у записи ссылкой сначала адрес, потом текст дня — ищем не с начала
        if mark in text:
            return day
    return None


def classify(message) -> tuple[int, int, str] | None:
    u"""(человек, день, вид) — если это отправленная ботом запись дня.

    Вид video — запись видео; link — ссылка на страницу сайта (так дни
    уходили 2–5 сентября). Текст дня без записи и без ссылки (запись ещё
    не залили) — не запись: закрывать там нечего.
    """
    if message is None or not getattr(message, 'out', False):
        return None
    user_id = getattr(getattr(message, 'peer_id', None), 'user_id', None)
    if not user_id:
        return None
    text = getattr(message, 'message', None) or u''
    day = day_of(text)
    if not day:
        return None
    if getattr(message, 'video', None) or getattr(message, 'document', None):
        return user_id, day, 'video'
    if u'http' in text:
        return user_id, day, 'link'
    return None


async def scan(client, upper: int) -> list[tuple[int, int, int, str, float]]:
    u"""Пройти номера сообщений бота: [(человек, день, номер, вид, когда)].

    upper — последний номер, который бот и так знает (из переписки пульта):
    до него идём, даже если попалась полоса пустых номеров (удалённые
    переписки), дальше — пока не пойдут одни пустые пачки.
    """
    found = []
    start, empty = 1, 0
    while start <= upper or empty < EMPTY_RUNS:
        batch = await client.get_messages(None, ids=list(range(start, start + BATCH)))
        got = [m for m in batch if m is not None]
        # пустые пачки считаются только за известным номером: до него это
        # удалённые переписки, а не конец истории
        if got:
            empty = 0
        elif start > upper:
            empty += 1
        for message in got:
            hit = classify(message)
            if hit:
                user_id, day, kind = hit
                found.append((user_id, day, message.id, kind, message.date.timestamp()))
        start += BATCH
        await asyncio.sleep(PAUSE)
    return found


async def _client():
    u"""Бот по MTProto — только читать, обновления этому входу не нужны."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), config.TG_API_ID, config.TG_API_HASH,
                            receive_updates=False)
    # Telegram притормаживает частые запросы — ждём, а не падаем.
    client.flood_sleep_threshold = 15 * 60
    await client.start(bot_token=config.BOT_TOKEN)
    return client


async def backfill(bot) -> int | None:
    u"""Найти старые записи и запомнить их номера. None — не запускался."""
    if db.get_content(DONE):
        return None
    if not (config.TG_API_ID and config.TG_API_HASH):
        log.info(u'TG_API_ID/TG_API_HASH не заданы — старые записи дней в переписках не ищем')
        return None
    log.info(u'ищем записи дней в старых переписках')
    client = await _client()
    try:
        found = await scan(client, db.max_message_id())
    finally:
        await client.disconnect()
    people = set()
    for user_id, day, tg_id, kind, when in found:
        db.remember_day_message(user_id, day, tg_id, kind, at=when)
        people.add(user_id)
    db.put_content(DONE, 'history', str(len(found)))
    log.info(u'в старых переписках записей дней: %d у %d чел.', len(found), len(people))
    await delivery.alert_admins(bot, texts.HISTORY_FOUND.format(messages=len(found),
                                                                people=len(people)))
    return len(found)


async def run(bot) -> None:
    u"""Фоном после запуска. Сбой — админам и в журнал; следующий запуск
    бота попробует снова: отметка ставится только после удачного прохода."""
    try:
        await backfill(bot)
    except Exception as err:
        log.exception(u'поиск старых записей дней не удался')
        try:
            await delivery.alert_admins(
                bot, texts.HISTORY_FAILED.format(why=html.escape(str(err))[:300]))
        except Exception:
            pass
