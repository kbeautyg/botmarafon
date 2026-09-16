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
import html
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot

from . import config, contact, db

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
    'zayavka': u'Заявка с сайта',
    # AleX 14.09.2026 раздаёт ссылки со своими короткими метками — без
    # расшифровки в статистике стояло бы «az», «ls», «nc»
    'az': u'Автообзвон',
    'ls': u'Рассылка в ЛС',
    'nc': u'Нейрокомментинг',
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
    u"""«Сайт ← Instagram» для site_ig, «Telegram ← рассылка1» для tg_рассылка1.

    Хвост после подчёркивания — название конкретной рассылки или
    объявления: AleX 09.09.2026 гоняет их несколько разом и сравнивает
    между собой. Имя после подчёркивания произвольное, поэтому если оно
    не из известных — показываем как есть, а не прячем.
    """
    if not source:
        return DIRECT
    if '_' in source:
        head, tail = source.split('_', 1)
        head = ALIASES.get(head, head)
        if head in LABELS:
            tail = ALIASES.get(tail, tail)
            return u'%s ← %s' % (LABELS[head], LABELS.get(tail, tail))
    return LABELS.get(source, source)


def breakdown(pairs: list[tuple[str, int]]) -> str:
    return u', '.join(u'%s %d' % (label(src), n) for src, n in pairs) if pairs else u'—'


def period_line(title: str, since: float) -> str:
    pairs = db.new_users(since)
    total = sum(n for _, n in pairs)
    return u'<b>%s:</b> +%d · запустили %d\n%s' % (title, total, db.launched_since(since),
                                                    breakdown(pairs))


def pct(part: int, whole: int) -> str:
    return u'%d%%' % round(part * 100.0 / whole) if whole else u'—'


def funnel_lines(since: float) -> str:
    u"""Что дала каждая ссылка: путь от захода до покупки, строкой на источник.

    Сравнивать рассылки по одному «пришло» бесполезно — важно, сколько из
    пришедших вообще запустились и сколько досмотрели до конца. Проценты
    считаем от пришедших: это и есть качество ссылки.
    """
    rows = db.source_funnel(since)
    if not rows:
        return u'За этот срок никто не заходил.'
    out = []
    for r in rows:
        people = r['people']
        line = u'<b>%s</b> — пришло %d' % (label(r['src']), people)
        line += u'\n   запустили %d (%s)' % (r['launched'], pct(r['launched'], people))
        line += u' · отвечали %d' % r['active']
        line += u' · дошли %d (%s)' % (r['finished'], pct(r['finished'], people))
        if r['buys']:
            line += u' · <b>нажали «купить» %d</b>' % r['buys']
        out.append(line)
    return u'\n'.join(out)


def report() -> str:
    u"""Ответ на /stats: сегодня, неделя, всё время + качество источников."""
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
        u'📈 <b>Что дала каждая ссылка за 7 дней</b>\n%s' % funnel_lines(week),
        u'Кто именно заходил — /кто. Готовые ссылки для рассылок — /ссылки.',
    ])


# Докуда дошёл человек — по строкам воронки insights.FUNNEL (reached).
PASSED = (u'—', u'запуск', u'день 1', u'день 2', u'день 3', u'день 4',
          u'кнопки покупки', u'«купить»')

# Где человек сейчас — ключи insights.POSITIONS, но про одного и без рода.
PLACE = {
    'launch': u'смотрит приветствие и отзывы, ждёт 1-й день',
    'q1': u'1-й день получен, ждёт вопроса',
    'a1': u'не ответил на «посмотрел?» после 1-го дня',
    'd2': u'ждёт 2-й день',
    'q2': u'2-й день получен, ждёт вопроса',
    'a2': u'не ответил на «посмотрел?» после 2-го дня',
    'd3': u'ждёт 3-й день',
    'q3': u'3-й день получен, ждёт вопроса',
    'a3': u'не ответил на «посмотрел?» после 3-го дня',
    'd4': u'ждёт 4-й день',
    'offer_wait': u'4-й день получен, ждёт кнопок покупки',
    'offer_got': u'кнопки покупки получены',
    'done': u'марафон закончился, «купить» не нажал',
    'bought': u'нажата «купить»',
    'banned': u'в чёрном списке — бот его не слушает',
    'blocked': u'заблокировал бота',
    'lead': u'заявка с сайта, ждёт менеджера',
    'not_launched': u'марафон не запущен',
    'idle': u'марафон прервался — бот ему больше не пишет',
}

# Начал до учёта шагов (11.09) и бот ему больше не пишет: марафон прервался
# на сбое, человек заблокировал бота или прошёл всё молча — по базе не
# различить (ревью 11.09.2026).
IDLE_BEFORE_TRACKING = (u'бот больше не пишет: марафон прервался (сбой или блокировка) '
                        u'или пройден молча — по базе не различить')

WHO_FOOTER = (
    u'ℹ️ «Заблокировал бота» видно, только когда бот пытается отправить следующий '
    u'шаг, — в скобках, когда бот это заметил. Тем, кто прошёл марафон, нажал «купить», '
    u'пришёл по заявке, не запустил марафон или у кого марафон прервался, бот больше '
    u'не пишет: заблокировали ли они его, неизвестно. До 11.09 шаги и блокировки не '
    u'записывались — у начавших раньше «или дальше» значит, что точный шаг неизвестен.')
CHUNK = 3900                      # запас до 4096 — предела сообщения Telegram


def source_label(u: dict) -> str:
    u"""Источник с номером заявки: «Заявка с сайта №65» — сверяется с сайтом
    (AleX 11.09.2026 искал людей из бота в отчёте сайта и не нашёл)."""
    text = label(u.get('source') or '')
    if u.get('lead_no'):
        text += u' №%d' % u['lead_no']
    return text


def _who_entry(r: dict, p: dict | None) -> str:
    u"""Две строки на человека: кто и откуда; докуда дошёл и где сейчас."""
    when = datetime.fromtimestamp(r['started_at'], MSK).strftime('%d.%m %H:%M')
    # имя ссылкой на профиль: у многих нет ника, а написать надо (AleX 13.09)
    who = contact.link(r['user_id'], r['first_name'])
    handle = u'@%s' % r['username'] if r['username'] else u'без ника'
    head = u'%s · %s · %s · %s' % (when, who, handle, source_label(r))
    if p is None:
        return head + u'\n   ↳ команда проекта'
    from . import insights          # insights сам импортирует stats — поэтому здесь

    position = p['position']
    place = PLACE.get(position, position)
    passed = PASSED[p['reached']]
    launched = p['u'].get('launched_at')
    logged = p['days'] or p['polls'] or p['offer']
    if launched and launched < insights.TRACK_TS and not (p['pending'] or p['buys'] or logged):
        # До учёта шагов молча пройденные дни следа не оставили: это нижняя граница.
        if p['reached'] < insights.OFFER:
            passed += u' или дальше'
        if position == 'idle':
            place = IDLE_BEFORE_TRACKING
    blocked = p['u'].get('blocked_at')
    if blocked:
        place += u' (бот заметил %s)' % _moment(blocked)
    nxt = p['next']
    if position in ('a1', 'a2', 'a3') and nxt and nxt['chain'].endswith('_no'):
        # не застрял: без ответа ветка «нет» сама пришлёт следующий день
        place += u' · %d-й день придёт сам %s' % (int(position[1:]) + 1, _moment(nxt['run_at']))
    return head + u'\n   ↳ пройдено: %s · сейчас: %s' % (passed, place)


def _moment(ts: float) -> str:
    return datetime.fromtimestamp(ts, MSK).strftime('%d.%m %H:%M')


def who_messages(limit: int = 30) -> list[str]:
    u"""Ответ на /кто сообщениями не длиннее предела Telegram.

    AleX 09.09.2026 просил видеть не только числа, но и людей: «наблюдали,
    кто в него заходил». 10.09.2026 — ещё и «напротив каждого, до какого
    шага дошёл, где остановился и вышел, заблокировал ли бота». Путь
    считается тем же расчётом, что в подробной статистике (bot/insights.py).
    """
    from . import insights          # insights сам импортирует stats — поэтому здесь

    rows = db.recent_users(limit)
    if not rows:
        return [u'В бота ещё никто не заходил.']
    people = insights.model()['people']
    chunks, current = [], u'👥 <b>Кто заходил в бота</b> — последние %d' % len(rows)
    for r in rows:
        entry = _who_entry(r, people.get(r['user_id']))
        if len(current) + 1 + len(entry) > CHUNK:
            chunks.append(current)
            current = entry
        else:
            current += u'\n' + entry
    if len(current) + 2 + len(WHO_FOOTER) > CHUNK:
        chunks.append(current)
        current = WHO_FOOTER
    else:
        current += u'\n\n' + WHO_FOOTER
    chunks.append(current)
    return chunks


def who_report(limit: int = 30) -> str:
    u"""То же одним текстом — для проверок и выгрузок."""
    return u'\n'.join(who_messages(limit))


def links_report(username: str) -> str:
    u"""Готовые ссылки под рассылки: скопировал — и в пост.

    Метка после «tg_» произвольная: сколько рассылок, столько и меток.
    Главное — у каждой своя, иначе в сводке они сольются в одну строку.
    """
    base = u'https://t.me/%s?start=' % username
    rows = [
        (u'Telegram — рассылка №1', 'tg_1'),
        (u'Telegram — рассылка №2', 'tg_2'),
        (u'Telegram — свой канал', 'tg_kanal'),
        (u'Telegram Ads', 'tg_ads1'),
        (u'Автообзвон', 'az'),
        (u'Рассылка в личные сообщения', 'ls'),
        (u'Нейрокомментинг', 'nc'),
        (u'Instagram — автоответ в директ по слову «Марафон»', 'ig_direct'),
        (u'Instagram — ссылка в шапке профиля', 'ig_bio'),
        (u'Instagram — прочее', 'ig'),
        (u'Facebook', 'fb'),
        (u'Сайт', 'site'),
    ]
    body = u'\n'.join(u'%s\n<code>%s%s</code>' % (title, base, tag) for title, tag in rows)
    return (u'🔗 <b>Ссылки на бота с метками</b>\n\n%s\n\n'
            u'Метку после <code>tg_</code> придумывайте любую — латиницей, цифрами, '
            u'без пробелов: <code>%stg_storis5</code>. В сводке она встанет отдельной '
            u'строкой, и будет видно, какая рассылка сработала лучше.' % (body, base))


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
