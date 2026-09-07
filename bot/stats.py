# -*- coding: utf-8 -*-
u"""Откуда приходят люди и сколько их — источники и сводка.

AleX 07.09.2026: «есть статистика, кто и из какого источника перешёл в
бота — что из телеграма, что из инстаграма, что из фейсбука?» и «чат,
куда бот каждый час скидывал бы, сколько добавилось новых».

Источник — хвост ссылки на бота: t.me/finish_marafon_bot?start=ig.
Telegram отдаёт его первым сообщением /start ig, и бот запоминает его за
человеком. Какую ссылку куда поставить — в README, раздел «Источники».
Сайт ставит свою метку сам: site, а если человек пришёл на сайт с
рекламы — site_fb, site_ig (js/analytics.js на лендинге).

Сводка уходит в STATS_CHAT_ID в начале каждого часа по Москве. Пустые
часы молчат: ночью в чате иначе было бы восемь сообщений «+0».
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from . import config, db

log = logging.getLogger(__name__)

MSK = ZoneInfo('Europe/Moscow')
HOUR = 3600

# Метка → как называть в сводке. Неизвестная метка показывается как есть.
LABELS = {
    'site': u'Сайт',
    'tg': u'Telegram',
    'ig': u'Instagram',
    'fb': u'Facebook',
    'yt': u'YouTube',
    'vk': u'ВКонтакте',
    'tt': u'TikTok',
}
# Полные имена, которые приходят из utm-меток сайта, — к коротким.
ALIASES = {
    'instagram': 'ig', 'insta': 'ig', 'facebook': 'fb', 'meta': 'fb',
    'telegram': 'tg', 'youtube': 'yt', 'tiktok': 'tt', 'vkontakte': 'vk',
}
DIRECT = u'напрямую'


def parse_source(payload: str | None) -> str:
    u"""Метка из хвоста /start: латиница, цифры, _ и -, не длиннее 32."""
    raw = (payload or '').strip().lower()
    if not raw or not all(ch.isalnum() or ch in '_-' for ch in raw) or len(raw) > 32:
        return ''
    return ALIASES.get(raw, raw)


def label(source: str) -> str:
    u"""«Сайт ← Instagram» для site_ig, «Instagram» для ig, метка как есть иначе."""
    if not source:
        return DIRECT
    if source.startswith('site_'):
        tail = source[5:]
        tail = ALIASES.get(tail, tail)
        return u'%s ← %s' % (LABELS['site'], LABELS.get(tail, tail))
    return LABELS.get(source, source)


def breakdown(pairs: list[tuple[str, int]]) -> str:
    return u', '.join(u'%s %d' % (label(src), n) for src, n in pairs) if pairs else u'—'


def period_line(title: str, since: float) -> str:
    pairs = db.new_users(since)
    total = sum(n for _, n in pairs)
    return u'<b>%s:</b> +%d · запустили %d\n%s' % (title, total, db.launched_since(since),
                                                    breakdown(pairs))


def report() -> str:
    u"""Ответ на /stats: сегодня, неделя, всё время."""
    now = datetime.now(MSK)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    week = (now - timedelta(days=7)).timestamp()
    counters = db.stats()
    return u'\n\n'.join([
        u'📊 <b>Откуда приходят в бота</b>',
        period_line(u'Сегодня', today),
        period_line(u'7 дней', week),
        u'<b>Всё время:</b> %d человек, запустили %d\n%s'
        % (counters['users'], counters['launched'], breakdown(db.new_users(0))),
    ])


def hourly(now: float | None = None) -> str | None:
    u"""Сводка за прошедший час; None — час был пустой, слать нечего."""
    now = now or time.time()
    since = now - HOUR
    pairs = db.new_users(since)
    total = sum(n for _, n in pairs)
    if not total:
        return None
    start = datetime.fromtimestamp(since, MSK)
    end = datetime.fromtimestamp(now, MSK)
    counters = db.stats()
    return (u'📊 <b>Марафон · %s–%s МСК</b>\n'
            u'Новых в боте: +%d — %s\n'
            u'Запустили марафон: +%d\n'
            u'Всего: %d в боте, %d запустили'
            % (start.strftime('%H:%M'), end.strftime('%H:%M'), total, breakdown(pairs),
               db.launched_since(since), counters['users'], counters['launched']))


def seconds_to_next_hour(now: datetime | None = None) -> float:
    now = now or datetime.now(MSK)
    target = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1.0, (target - now).total_seconds())


async def loop(bot: Bot) -> None:
    u"""Каждый час по Москве — сводка в чат, если за час кто-то пришёл."""
    if not config.STATS_CHAT_ID:
        log.info(u'сводка по источникам выключена: STATS_CHAT_ID пуст')
        return
    log.info(u'сводка по источникам: каждый час в чат %s', config.STATS_CHAT_ID)
    while True:
        await asyncio.sleep(seconds_to_next_hour())
        try:
            text = hourly()
            if text:
                await bot.send_message(config.STATS_CHAT_ID, text)
        except Exception as err:                       # чат мог удалить бота — не падаем
            log.warning(u'сводка не ушла: %s', err)
