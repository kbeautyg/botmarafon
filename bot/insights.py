# -*- coding: utf-8 -*-
u"""Подробная статистика марафона — разделы с кнопками и выгрузка таблицей.

Sharp 11.09.2026: «статистика в боте маленькая, сильно расширь, чтоб было
много данных». Прежняя /stats считала только «пришло» и «запустили»: дальше
бот был слеп — нигде не записывалось, кто какой день получил и кто закрыл
бота. С этого дня планировщик пишет каждый шаг воронки в events, а уход из
бота — в users.blocked_at (bot/scheduler.py).

Путь людей, начавших до учёта шагов, восстанавливается по косвенным
признакам: ответы на вопросы, очередь шагов, покупки. Кто прошёл всё молча,
без ответов, там не виден — это честно сказано под сводкой.

Команда проекта (ADMIN_IDS и STATS_IDS) в цифры не входит: её тестовые
прогоны раздували бы воронку.
"""
import csv
import html
import io
import statistics
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db, funnel, stats

MSK = ZoneInfo('Europe/Moscow')
DAY = 86400
LIMIT = 3900                      # запас до 4096 — предела сообщения Telegram
TRACK_SINCE = u'11.09.2026'
TRACK_TS = datetime(2026, 9, 11, tzinfo=ZoneInfo('Europe/Moscow')).timestamp()

SECTIONS = {
    'sum': u'Сводка',
    'fun': u'Воронка',
    'src': u'Источники',
    'days': u'Дни и опросы',
    'time': u'По времени',
    'buy': u'Покупки',
    'now': u'Где люди сейчас',
}
PERIODS = {'d': u'сегодня', '7': u'за 7 дней', '30': u'за 30 дней', 'all': u'за всё время'}
PERIOD_BUTTONS = (('d', u'Сегодня'), ('7', u'7 дней'), ('30', u'30 дней'), ('all', u'Всё время'))

# Воронка по порядку. Номер строки — «докуда дошёл» (reached).
FUNNEL = (
    ('came', u'пришли'),
    ('launched', u'запустили'),
    ('day1', u'день 1'),
    ('day2', u'день 2'),
    ('day3', u'день 3'),
    ('day4', u'день 4'),
    ('offer', u'кнопки покупки'),
    ('bought', u'нажали «купить»'),
)
LAUNCHED, OFFER, BOUGHT = 1, 6, 7
PRODUCT_NAMES = {'gym': u'энерго спортзал', 'course': u'обучение'}
WEEKDAYS = (u'пн', u'вт', u'ср', u'чт', u'пт', u'сб', u'вс')

# Где человек сейчас — в порядке воронки.
POSITIONS = (
    ('launch', u'смотрят приветствие и отзывы, ждут 1-й день'),
    ('q1', u'получили 1-й день, ждут вопроса'),
    ('a1', u'не ответили на вопрос после 1-го дня'),
    ('d2', u'ждут 2-й день'),
    ('q2', u'получили 2-й день, ждут вопроса'),
    ('a2', u'не ответили на вопрос после 2-го дня'),
    ('d3', u'ждут 3-й день'),
    ('q3', u'получили 3-й день, ждут вопроса'),
    ('a3', u'не ответили на вопрос после 3-го дня'),
    ('d4', u'ждут 4-й день'),
    ('offer_wait', u'получили 4-й день, ждут кнопок покупки'),
    ('offer_got', u'получили кнопки покупки'),
    ('done', u'прошли весь марафон'),
    ('bought', u'нажали «купить»'),
    ('blocked', u'закрыли бота'),
    ('lead', u'пришли по заявке с сайта — ждут менеджера'),
    ('not_launched', u'зашли, но не запустили марафон'),
    ('idle', u'без шагов в очереди — запускали до учёта шагов'),
)


# ------------------------------------------------------------ модель

def _day_of(poll) -> int:
    u"""«day2» → 2; всё прочее → 0."""
    text = str(poll or '')
    return int(text[3:]) if text.startswith('day') and text[3:].isdigit() else 0


def _reached(p: dict) -> int:
    u"""Докуда дошёл человек — номер строки FUNNEL.

    Источники по убыванию точности: записанные шаги (events), ответы на
    вопросы, ожидающий вопрос, очередь шагов, покупки. Дошёл до дня N —
    значит, прошёл и все дни до него: берём максимум.
    """
    u = p['u']
    days = set(p['days'])
    days |= {_day_of(k) for k in p['polls']}
    days.add(_day_of(u.get('poll')))
    # Ответ на вопрос N запускает ветку, которая через секунды шлёт день N+1.
    # Ветки в очереди больше нет — значит, день N+1 уже ушёл (ревью 11.09).
    for poll in p['answers']:
        n = _day_of(poll)
        if n:
            days.add(n)
            branches = set(funnel.POLL_BRANCHES.get(poll, {}).values())
            if not branches & p['pending']:
                days.add(n + 1)
    if p['pending']:
        days |= {n for n in (1, 2, 3, 4) if funnel.day_delivered(p['pending'], n)}
    days.discard(0)
    reached = LAUNCHED if u.get('launched_at') else 0
    if days:
        reached = max(reached, 1 + min(max(days), 4))
    # Очередь пуста после 4-го дня — единственным продолжением были кнопки
    # покупки (after_day4). У начавших до учёта шагов это и есть «дошёл».
    if 4 in days and not p['pending'] and u.get('launched_at') and not u.get('blocked_at'):
        reached = max(reached, OFFER)
    if p['offer']:
        reached = max(reached, OFFER)
    if p['buys']:
        reached = max(reached, BOUGHT)
    return reached


def _position(p: dict) -> str:
    u"""Где человек сейчас — ключ из POSITIONS."""
    u = p['u']
    if u.get('blocked_at'):
        return 'blocked'
    # Вопрос живой, только пока в очереди ждёт ветка «нет»: без неё он
    # устарел — бот уже повёл человека дальше сам (ревью 11.09).
    fallback = funnel.POLL_BRANCHES.get(u.get('poll') or '', {}).get('no')
    if u.get('poll') and (config.POLL_FALLBACK_HOURS <= 0 or fallback in p['pending']):
        return 'a%d' % _day_of(u['poll'])
    nxt = p['next']
    if nxt:
        chain = nxt['chain']
        if chain == 'launch':
            return 'launch'
        if chain.startswith('after_day'):
            k = _day_of(chain[len('after_'):])
            if k == 4:
                return 'offer_got' if nxt.get('pos', 0) >= 2 else 'offer_wait'
            return 'q%d' % k
        if chain.endswith('_yes') or chain.endswith('_no'):
            return 'd%d' % (_day_of(chain.split('_')[0]) + 1)
    if p['buys']:
        return 'bought'
    if p['reached'] >= 5:
        return 'done'
    if not u.get('launched_at'):
        return 'lead' if u.get('source') == 'zayavka' else 'not_launched'
    return 'idle'


def model(snap: dict | None = None) -> dict:
    u"""Люди с их путём. snap — db.snapshot(); по умолчанию читается сейчас."""
    snap = db.snapshot() if snap is None else snap
    team = set(config.ADMIN_IDS) | set(config.STATS_IDS)
    people, skipped = {}, 0
    for u in snap['users']:
        if u['user_id'] in team:
            skipped += 1
            continue
        people[u['user_id']] = {
            'u': u, 'days': set(), 'day_at': {}, 'polls': set(), 'poll_at': {},
            'offer': False, 'offer_at': None, 'answers': {}, 'buys': [],
            'pending': set(), 'next': None, 'care': 0, 'missed': set()}

    for e in snap['events']:
        p = people.get(e['user_id'])
        if not p:
            continue
        ref = str(e['ref'] or '')
        if e['kind'] == 'day' and ref.isdigit():
            p['days'].add(int(ref))
            p['day_at'].setdefault(int(ref), e['at'])
        elif e['kind'] == 'poll' and _day_of(ref):
            p['polls'].add(ref)
            p['poll_at'].setdefault(ref, e['at'])
        elif e['kind'] == 'offer':
            p['offer'] = True
            p['offer_at'] = p['offer_at'] or e['at']
    for a in snap['answers']:
        if a['user_id'] in people:
            people[a['user_id']]['answers'][a['poll']] = (a['answer'], a['answered'])
    for b in snap['purchases']:
        if b['user_id'] in people:
            people[b['user_id']]['buys'].append(b)
    for j in snap['jobs']:
        p = people.get(j['user_id'])
        if p:
            p['pending'].add(j['chain'])
            if p['next'] is None or j['run_at'] < p['next']['run_at']:
                p['next'] = j
    for c in snap['care']:
        if c['user_id'] in people:
            people[c['user_id']]['care'] = c['n']
    for m in snap['missed']:
        if m['user_id'] in people:
            people[m['user_id']]['missed'].add(m['day'])

    for p in people.values():
        p['reached'] = _reached(p)
        p['position'] = _position(p)
    return {'people': people, 'team': skipped}


# ------------------------------------------------------------ расчёты

def bounds(period: str, now: float) -> tuple[float, float, float | None]:
    u"""(от, до, от прошлого такого же периода) — для сравнения."""
    if period == 'd':
        start = datetime.fromtimestamp(now, MSK).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        return start, now, None           # полдня с сутками не сравнить
    if period in ('7', '30'):
        span = int(period) * DAY
        return now - span, now, now - 2 * span
    return 0.0, now, None


LEAD_SOURCE = 'zayavka'


def cohort(m: dict, lo: float, hi: float, leads: bool = False) -> list[dict]:
    u"""Кто пришёл в бота за [lo, hi).

    Люди со ссылки из заявки сайта (?start=zayavka) марафон не запускают —
    они ждут менеджера по спортзалу, — и в воронке марафона только тянули бы
    вниз «запустили». Поэтому они считаются отдельно: leads=True — только они.
    """
    return [p for p in m['people'].values()
            if lo <= (p['u'].get('started_at') or 0) < hi
            and (p['u'].get('source') == LEAD_SOURCE) == leads]


def reach_counts(group: list[dict]) -> list[int]:
    u"""Сколько дошло до каждой строки воронки."""
    return [sum(1 for p in group if p['reached'] >= i) for i in range(len(FUNNEL))]


def _moving(p: dict) -> bool:
    u"""Ещё идёт по расписанию: есть шаги в очереди и бота не закрыл."""
    return bool(p['pending']) and not p['u'].get('blocked_at')


def stopped_counts(group: list[dict]) -> list[int]:
    u"""Сколько остановилось на каждой строке: дошли до неё и дальше не идут.

    Идущие по расписанию не потеряны — весь путь занимает от шести часов до
    полутора суток, и сегодняшние люди просто ещё в дороге (ревью 11.09).
    """
    out = [0] * len(FUNNEL)
    for p in group:
        if not _moving(p):
            out[p['reached']] += 1
    return out


def _pct(part: float, whole: float) -> str:
    return u'%d%%' % round(part * 100.0 / whole) if whole else u'—'


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ''))


def _when(ts: float | None, fmt: str = '%d.%m %H:%M') -> str:
    return datetime.fromtimestamp(ts, MSK).strftime(fmt) if ts else u''


def _span(seconds: float) -> str:
    u"""Длительность по-человечески: «2 ч 10 мин», «1 дн 3 ч»."""
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return u'%d мин' % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return u'%d ч %d мин' % (hours, minutes)
    days, hours = divmod(hours, 24)
    return u'%d дн %d ч' % (days, hours)


def _bar(n: int, top: int, width: int = 8) -> str:
    filled = int(round(width * n / top)) if top else 0
    return u'█' * filled + u'·' * (width - filled)


def _fit(lines: list[str]) -> str:
    u"""Склеить строки, не выйдя за предел сообщения и не разорвав <pre>."""
    out, size, in_pre = [], 0, False
    for line in lines:
        if size + len(line) + 1 > LIMIT - 20:
            if in_pre:
                out.append(u'</pre>')
            out.append(u'…')
            break
        out.append(line)
        size += len(line) + 1
        if u'<pre>' in line:
            in_pre = True
        if u'</pre>' in line:
            in_pre = False
    return u'\n'.join(out)


def _head(title: str, period: str | None) -> str:
    tail = u' · %s' % PERIODS[period] if period else u''
    return u'📊 <b>%s</b>%s' % (title, tail)


NOTE = (u'<i>Команда проекта не считается. Шаги воронки записываются с %s — '
        u'у начавших раньше путь виден по ответам и очереди, кто прошёл всё '
        u'молча, там не виден.</i>' % TRACK_SINCE)


# ------------------------------------------------------------ разделы

def section_sum(m: dict, period: str, now: float) -> str:
    lo, hi, prev_lo = bounds(period, now)
    g = cohort(m, lo, hi)
    c = reach_counts(g)
    prev = len(cohort(m, prev_lo, lo)) if prev_lo is not None else None
    delta = u''
    if prev:
        delta = u' <i>(%+d%% к прошлым %s дн.)</i>' % (
            round((c[0] - prev) * 100.0 / prev), period)
    # по людям, а не по нажатиям: кнопку жмут и дважды
    products = {}
    for p in g:
        for key in {b['product'] for b in p['buys']}:
            products[key] = products.get(key, 0) + 1
    bought_line = u', '.join(u'%s %d' % (PRODUCT_NAMES.get(k, _esc(k)), n)
                             for k, n in sorted(products.items()))
    everyone = list(m['people'].values())
    running = sum(1 for p in everyone if _moving(p))
    waiting = sum(1 for p in everyone if p['position'].startswith('a'))

    lines = [_head(u'Марафон — сводка', period), u'',
             u'👥 Пришли в бота: <b>%d</b>%s' % (c[0], delta),
             u'▶️ Запустили марафон: <b>%d</b> — %s от пришедших' % (c[1], _pct(c[1], c[0])),
             u'📅 Дошли до 2-го дня: <b>%d</b> — %s от запустивших' % (c[3], _pct(c[3], c[1])),
             u'🏁 Дошли до 4-го дня: <b>%d</b> — %s' % (c[5], _pct(c[5], c[1])),
             u'🎯 Получили кнопки покупки: <b>%d</b>' % c[6],
             u'🛒 Нажали «купить»: <b>%d</b>%s' % (c[7], (u' — ' + bought_line) if bought_line else u''),
             u'🚪 Закрыли бота: %d' % sum(1 for p in g if p['u'].get('blocked_at')),
             u'🤝 Писали в службу заботы: %d' % sum(1 for p in g if p['care']),
             u'📨 Пришли по заявке с сайта (ждут менеджера, в воронку не входят): %d'
             % len(cohort(m, lo, hi, leads=True)),
             u'',
             u'⏳ Сейчас идут марафон: <b>%d</b> · не ответили на вопрос: %d' % (running, waiting),
             u'🗂 <b>Всё время:</b> %d человек, запустили %d'
             % (len(everyone), sum(1 for p in everyone if p['u'].get('launched_at')))]

    by_source = {}
    for p in g:
        by_source.setdefault(p['u'].get('source') or '', []).append(p)
    # Откуда пришли — одной строкой прямо в сводке (так было в прежней
    # /stats, и первым делом смотрят именно сюда); подробно — «Источники».
    pairs = sorted(((src, len(grp)) for src, grp in by_source.items()), key=lambda kv: (-kv[1], kv[0]))
    if pairs:
        tail = u' · …' if len(pairs) > 8 else u''
        lines.append(u'🔗 Откуда: %s%s' % (_esc(stats.breakdown(pairs[:8])), tail))
    rated = []
    for src, group in by_source.items():
        sc = reach_counts(group)
        if sc[1] >= 5:
            rated.append((sc[5] * 1.0 / sc[1], sc[1], src))
    if rated:
        rate, launched, src = max(rated)
        lines.append(u'🏆 Лучше всех доходит до 4-го дня: <b>%s</b> — %d%% из %d запустивших'
                     % (_esc(stats.label(src)), round(rate * 100), launched))
    stopped = stopped_counts(g)
    drops = [(stopped[i - 1], i) for i in range(2, len(FUNNEL))]
    lost, i = max(drops) if drops else (0, 0)
    if lost > 0:
        lines.append(u'📍 Больше всего теряем: «%s» → «%s», −%d (%s)'
                     % (FUNNEL[i - 1][1], FUNNEL[i][1], lost, _pct(lost, c[i - 1])))
    lines += [u'', NOTE]
    return _fit(lines)


def section_fun(m: dict, period: str, now: float) -> str:
    lo, hi, _ = bounds(period, now)
    group = cohort(m, lo, hi)
    c = reach_counts(group)
    stopped = stopped_counts(group)
    moving = sum(1 for p in group if _moving(p))
    lines = [_head(u'Воронка', period),
             u'Из %d пришедших — докуда дошли; справа — сколько остановились перед шагом:' % c[0],
             u'<pre>']
    for i, (_, label) in enumerate(FUNNEL):
        lost = stopped[i - 1] if i else 0
        lines.append(_esc(u'%-15s %4d %4s %s %s' % (
            label, c[i], _pct(c[i], c[0]), _bar(c[i], c[0]), (u'−%d' % lost) if lost else u'')))
    lines.append(u'</pre>')
    if moving:
        lines.append(u'⏳ Ещё в пути по расписанию: %d — идут дальше, в потери не считаются.' % moving)
    if c[1]:
        lines.append(u'От запустивших до 4-го дня доходят %s, до кнопок — %s, покупают — %s.'
                     % (_pct(c[5], c[1]), _pct(c[6], c[1]), _pct(c[7], c[1])))
    lines += [u'', NOTE]
    return _fit(lines)


def section_src(m: dict, period: str, now: float) -> str:
    lo, hi, _ = bounds(period, now)
    groups = {}
    for p in cohort(m, lo, hi):
        groups.setdefault(p['u'].get('source') or '', []).append(p)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = [_head(u'Источники', period),
             u'По каждой ссылке: пришли → запустили → дошли до 2-го и 4-го дня → нажали «купить». '
             u'Проценты — от запустивших.', u'']
    if not ordered:
        lines.append(u'За этот срок никто не заходил.')
    for src, group in ordered[:15]:
        sc = reach_counts(group)
        lines.append(u'<b>%s</b> — пришли %d' % (_esc(stats.label(src)), sc[0]))
        lines.append(u'   ▶ %d · д2 %d (%s) · д4 %d (%s) · 🛒 %d'
                     % (sc[1], sc[3], _pct(sc[3], sc[1]), sc[5], _pct(sc[5], sc[1]), sc[7]))
    if len(ordered) > 15:
        lines.append(u'… и ещё %d источников — полностью в выгрузке таблицей.' % (len(ordered) - 15))
    lines += [u'', u'Своя метка на рассылку — /ссылки.']
    return _fit(lines)


def section_days(m: dict, period: str, now: float) -> str:
    lo, hi, _ = bounds(period, now)
    g = cohort(m, lo, hi)
    lines = [_head(u'Дни и опросы', period), u'']
    for n in (1, 2, 3, 4):
        got = [p for p in g if p['reached'] >= 1 + n]
        lines.append(u'<b>День %d</b> — получили %d' % (n, len(got)))
        if n <= 3:
            poll = 'day%d' % n
            asked = [p for p in got if poll in p['polls'] or poll in p['answers']
                     or p['u'].get('poll') == poll or p['reached'] >= 2 + n]
            yes = sum(1 for p in asked if p['answers'].get(poll, ('', 0))[0] == 'yes')
            no = sum(1 for p in asked if p['answers'].get(poll, ('', 0))[0] == 'no')
            lines.append(u'   «Посмотрел?»: ✅ да %d (%s) · ❌ нет %d · 🤐 молчат %d'
                         % (yes, _pct(yes, len(asked)), no, len(asked) - yes - no))
            delays = [p['answers'][poll][1] - p['poll_at'][poll] for p in asked
                      if poll in p['answers'] and poll in p['poll_at']
                      and p['answers'][poll][1] >= p['poll_at'][poll]]
            if delays:
                lines.append(u'   отвечают обычно через %s' % _span(statistics.median(delays)))
        else:
            offer = sum(1 for p in g if p['reached'] >= OFFER)
            bought = sum(1 for p in g if p['reached'] >= BOUGHT)
            lines.append(u'   → кнопки покупки %d → нажали «купить» %d' % (offer, bought))
    missed = [(n, sum(1 for p in g if n in p['missed'])) for n in (1, 2, 3, 4)]
    missed = [(n, k) for n, k in missed if k]
    if missed:
        lines += [u'', u'⚠️ Ушли без записи (видео ещё не было): '
                  + u', '.join(u'день %d — %d' % (n, k) for n, k in missed)
                  + u'. Дослать — /resend.']
    lines += [u'', u'<i>«Молчат» — не нажали ни «да», ни «нет»; через %d ч бот сам '
                   u'ведёт их дальше по ветке «нет».</i>' % config.POLL_FALLBACK_HOURS]
    return _fit(lines)


def section_time(m: dict, period: str, now: float) -> str:
    # заявки с сайта марафон не запускают — как и в воронке, считаем без них
    people = [p for p in m['people'].values() if p['u'].get('source') != LEAD_SOURCE]
    today = datetime.fromtimestamp(now, MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    lines = [_head(u'По времени', None),
             u'Последние 14 дней, время московское, без заявок с сайта:', u'<pre>',
             _esc(u'дата   пришли зап  д4 куп')]
    for back in range(13, -1, -1):
        start = (today - timedelta(days=back)).timestamp()
        end = start + DAY
        came = sum(1 for p in people if start <= (p['u'].get('started_at') or 0) < end)
        launched = sum(1 for p in people if start <= (p['u'].get('launched_at') or 0) < end)
        day4 = sum(1 for p in people if start <= p['day_at'].get(4, 0) < end)
        # до начала учёта шагов 4-й день не записывался — прочерк, а не ноль
        day4_cell = str(day4) if end > TRACK_TS else u'—'
        bought = sum(1 for p in people for b in p['buys'] if start <= b['at'] < end)
        label = (today - timedelta(days=back)).strftime('%d.%m')
        lines.append(_esc(u'%s %6d %4d %3s %3d' % (label, came, launched, day4_cell, bought)))
    lines.append(u'</pre>')
    lines.append(u'<i>«д4» — дошли до 4-го дня; записывается с %s.</i>' % TRACK_SINCE)

    since = now - 30 * DAY
    recent = [p for p in people if (p['u'].get('started_at') or 0) >= since]
    hours = [0] * 8
    weekdays = [0] * 7
    for p in recent:
        moment = datetime.fromtimestamp(p['u']['started_at'], MSK)
        hours[moment.hour // 3] += 1
        weekdays[moment.weekday()] += 1
    top = max(hours) if recent else 0
    lines += [u'Когда приходят в бота (за 30 дней, по 3 часа):', u'<pre>']
    for k, n in enumerate(hours):
        lines.append(_esc(u'%02d–%02d %s %d' % (k * 3, k * 3 + 3, _bar(n, top, 10), n)))
    lines += [u'</pre>',
              u'По дням недели: ' + u' · '.join(u'%s %d' % (WEEKDAYS[i], n)
                                               for i, n in enumerate(weekdays))]
    return _fit(lines)


def section_buy(m: dict, period: str, now: float) -> str:
    lo, hi, _ = bounds(period, now)
    people = list(m['people'].values())
    buys = [(b, p) for p in people for b in p['buys'] if lo <= b['at'] < hi]
    buyers = {p['u']['user_id'] for _, p in buys}
    c = reach_counts(cohort(m, lo, hi))
    lines = [_head(u'Покупки', period), u'',
             u'🛒 Нажали «купить»: <b>%d</b> человек, заявок %d' % (len(buyers), len(buys))]
    products = {}
    for b, _ in buys:
        products[b['product']] = products.get(b['product'], 0) + 1
    for key, n in sorted(products.items(), key=lambda kv: -kv[1]):
        lines.append(u'   — %s: %d' % (PRODUCT_NAMES.get(key, _esc(key)), n))
    if c[6]:
        lines.append(u'Из получивших кнопки (по пришедшим за срок) купили %s' % _pct(c[7], c[6]))
    paths = []
    for p in people:
        firsts = [b['at'] for b in p['buys'] if lo <= b['at'] < hi]
        start = p['u'].get('launched_at') or p['u'].get('started_at')
        if firsts and start and min(firsts) >= start:
            paths.append(min(firsts) - start)
    if paths:
        lines.append(u'От запуска до покупки обычно: %s' % _span(statistics.median(paths)))
    by_source = {}
    for _, p in buys:
        key = stats.label(p['u'].get('source') or '')
        by_source[key] = by_source.get(key, 0) + 1
    if by_source:
        ranked = sorted(by_source.items(), key=lambda kv: -kv[1])
        lines.append(u'По источникам: ' + u' · '.join(
            u'%s %d' % (_esc(k), n) for k, n in ranked[:10]) + (u' · …' if len(ranked) > 10 else u''))
    if buys:
        lines += [u'', u'<b>Последние:</b>']
        for b, p in sorted(buys, key=lambda bp: -bp[0]['at'])[:12]:
            u = p['u']
            handle = u'@%s' % u['username'] if u.get('username') else u'без ника'
            lines.append(u'%s · %s · %s · %s · %s' % (
                _when(b['at']), _esc(u.get('first_name') or u'без имени'), _esc(handle),
                PRODUCT_NAMES.get(b['product'], _esc(b['product'])),
                _esc(stats.label(u.get('source') or ''))))
    elif not products:
        lines.append(u'За этот срок покупок не было.')
    return _fit(lines)


def section_now(m: dict, period: str, now: float) -> str:
    people = list(m['people'].values())
    counts = {}
    for p in people:
        counts[p['position']] = counts.get(p['position'], 0) + 1
    lines = [_head(u'Где люди сейчас', None),
             u'Всего в боте: <b>%d</b> (команда проекта не считается). Период здесь не важен — '
             u'это состояние на сейчас.' % len(people), u'']
    for key, title in POSITIONS:
        if counts.get(key):
            lines.append(u'%s — <b>%d</b>' % (title, counts[key]))
    if not people:
        lines.append(u'В боте пока никого.')
    lines += [u'', u'Поимённо — /кто, всех сразу — выгрузка таблицей.']
    return _fit(lines)


RENDERERS = {
    'sum': section_sum, 'fun': section_fun, 'src': section_src, 'days': section_days,
    'time': section_time, 'buy': section_buy, 'now': section_now,
}


def render(section: str, period: str, snap: dict | None = None, now: float | None = None) -> str:
    u"""Текст раздела. Неизвестный раздел или период — сводка за 7 дней."""
    section = section if section in RENDERERS else 'sum'
    period = period if period in PERIODS else '7'
    now = time.time() if now is None else now
    return RENDERERS[section](model(snap), period, now)


# ------------------------------------------------------------ выгрузка

CSV_HEADER = (u'id', u'ник', u'имя', u'пришёл (МСК)', u'источник', u'запустил (МСК)',
              u'дошёл до', u'ответ после дня 1', u'ответ после дня 2', u'ответ после дня 3',
              u'купил', u'закрыл бота (МСК)', u'сейчас', u'писал в заботу')
ANSWER_NAMES = {'yes': u'да', 'no': u'нет'}
FORMULA_START = (u'=', u'+', u'-', u'@', u'\t', u'\r')


def _cell(value) -> str:
    u"""Текстовая ячейка, безопасная для Excel.

    Имя в Telegram может быть любым: начнись оно с «=», «+», «-» или «@» —
    Excel выполнит ячейку как формулу (так через таблицу подсовывают ссылки
    и команды). Апостроф в начале Excel и Google Таблицы прячут и
    показывают текст как есть.
    """
    text = u'' if value is None else str(value)
    return u"'" + text if text.startswith(FORMULA_START) else text


def csv_bytes(snap: dict | None = None) -> bytes:
    u"""Все люди марафона одной таблицей — для Excel (UTF-8 с BOM, «;»)."""
    m = model(snap)
    titles = dict(POSITIONS)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(CSV_HEADER)
    full = '%d.%m.%Y %H:%M'
    for p in sorted(m['people'].values(), key=lambda q: -(q['u'].get('started_at') or 0)):
        u = p['u']
        # Ник — без «@»: в таблице он просто текст, а «@» в начале ячейки
        # Excel принял бы за формулу.
        writer.writerow((
            u['user_id'], _cell(u.get('username') or u''),
            _cell(u.get('first_name') or u''), _when(u.get('started_at'), full),
            _cell(stats.label(u.get('source') or '')), _when(u.get('launched_at'), full),
            FUNNEL[p['reached']][1],
            ANSWER_NAMES.get(p['answers'].get('day1', ('', 0))[0], u''),
            ANSWER_NAMES.get(p['answers'].get('day2', ('', 0))[0], u''),
            ANSWER_NAMES.get(p['answers'].get('day3', ('', 0))[0], u''),
            _cell(u', '.join(u'%s %s' % (PRODUCT_NAMES.get(b['product'], b['product']),
                                         _when(b['at'], full)) for b in p['buys'])),
            _when(u.get('blocked_at'), full),
            titles.get(p['position'], u''),
            p['care'] or u''))
    return buf.getvalue().encode('utf-8-sig')
