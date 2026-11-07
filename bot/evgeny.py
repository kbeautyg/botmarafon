# -*- coding: utf-8 -*-
u"""Евгений — ИИ-помощник, который ведёт диалог и дожимает до спортзала.

AleX 11.10.2026: «в пульте — кнопочка (Ai) рядом с тем, что можно
прикрепить: она активирует Евгения и подключает к диалогу, чтобы он делал
дожимы»; и массово — пользователь 11.10.2026: «Евгений должен дожимать тех,
кто ничего после марафона не сделал».

Как устроено:
- Евгений пишет от имени бота. Его сообщения лежат в той же переписке, что
  и ответы команды (messages, author = EVGENY), — пульт показывает их с 🤖.
- Человек пишет боту — сообщение, как и раньше, уходит команде, а отвечает
  Евгений: ждёт DEBOUNCE секунд (человек часто дописывает вторым сообщением)
  и отвечает на всё разом.
- Факты о спортзале — только из texts.EVGENY_FACTS (или правки команды
  /евгений_факты); чего там нет — Евгений зовёт команду, а не выдумывает.
- Теги в ответе модели: [BUY] — под сообщением кнопка «Вступить…» (та же,
  что после марафона: заявка менеджерам и оплата идут как обычно), [TEAM] —
  нужен человек: команде уходит вопрос, Евгений в диалоге выключается,
  [STOP] — человек отказался: Евгений прощается и больше не пишет.
- Команда ответила человеку сама (реплаем или из пульта) — Евгений в этом
  диалоге выключается: двое отвечающих разом путают человека.
- Молчащему — до MAX_NUDGES напоминаний раз в сутки, только днём.
- Массовый дожим: первое сообщение — готовый текст (texts.EVGENY_OPENERS),
  по OPEN_PER_MINUTE в минуту и только днём; очередь в базе переживает
  перезапуск бота.

Координатор марафона (заказчик 06.11.2026): Евгений ведёт каждого с первого
дня, сам, без кнопки в пульте.
- Его сообщения по сценарию — шаги воронки evgeny (funnel.EVGENY_CHAINS):
  знакомство через минуту после записи первого дня; нажавшему «Да» или «Нет»
  под днём — через минуту после следующей записи вопрос, что он чувствовал.
  Рассказал об ощущениях сам (тег [FEEDBACK N]) — про этот день не спрашивает;
  рассказывал после прошлого дня — «так же жду обратную связь».
- Отвечает по «мозгам» заказчика (texts.EVGENY_SENSES): сторона тела — линия
  рода, что значат тяжесть, жар, мысли, картинки.
- Об оплате не говорит: тег [PAY] — команде уведомление с кнопкой в пульт;
  не напишет никто за PAY_WAIT — Евгений зовёт коллегу и напоминает команде.
- Без ключа ИИ или выключенный командой (/евгений) — молчит весь.

Проверка командой (Sharp 07.11.2026: «не запускай пока… нужно протестить
Евгения администрации… сейчас убери, чтобы клиенты с ним не общались»).
Участникам Евгений — и координатор, и дожим по кнопке 🤖 — пишет, только когда
команда его запустила (/евгений); до того — никому. Своим (config.is_team) —
всегда: кнопка «🤖 Проверить
Евгения» стирает прошлый разговор, Евгений знакомится заново и отвечает, как
участнику. Что получила бы команда, видит только проверяющий.
"""
import asyncio
import html
import logging
import random
import re
import time
from datetime import datetime

from . import ai, config, contact, db, delivery, keyboards, stats, texts

log = logging.getLogger(__name__)

EVGENY = -1                 # author в messages: написал Евгений, а не человек из команды
DEBOUNCE = 25.0             # секунд ждём, не допишет ли человек ещё
HISTORY = 40                # сколько последних сообщений переписки видит ИИ
DAY_RESEND_GAP = 6 * 3600   # одну и ту же запись дня Евгений шлёт человеку не чаще
TICK = 30                   # как часто проверять очередь и молчащих
NUDGE_AFTER = 24 * 3600     # напоминание молчащему — через сутки
MAX_NUDGES = 2
OPEN_PER_MINUTE = 20        # массовый дожим: первых сообщений в минуту
DAY4_WAIT = 72 * 3600       # «после марафона»: с 4-го дня прошло 72 часа (записи закрыты)
DAY_HOURS = (9, 21)         # писать первым — только с 9 до 21 по Москве
FACTS_KEY = 'evgeny:facts'
COACH_KEY = 'evgeny:coach'  # 'on' — команда включила координатора участникам (/евгений)
TRY_KEY = 'evgeny:try:%d'   # проверка командой: про какой день Евгений спросил своего (0 — знакомство)
TRY_IDLE = 24 * 3600        # проверку забросили на сутки — свой снова пишет боту как команда
PAY_WAIT = 5 * 60           # столько ждём, что про оплату человеку напишет команда
PAY_EPISODE = 3 * 3600      # то же оплатное дело: команде второй раз не пишем
TEAM_QUIET = 6 * 3600       # команда недавно писала человеку — координатор не встревает
# Так выключенного Евгения координатор сам не включает: человек просил не писать,
# команда выключила вручную.
HOLD = ('refused', 'manual')

_TAG = re.compile(r'\[(BUY|TEAM|STOP|PAY)\]([^\n\[]*)', re.I)
# [DAY 1] — прислать запись дня ещё раз (AleX 19.10.2026: «первый день не посмотрел»)
_DAY_TAG = re.compile(r'\[DAY\s*([1-4])\]', re.I)
# [FEEDBACK 2] — человек рассказал, что чувствовал после второго дня
_FEEDBACK_TAG = re.compile(r'\[FEEDBACK\s*([1-4])\]', re.I)
_KIND_NAMES = {'voice': u'голосовое сообщение', 'video_note': u'видео-кружок',
               'photo': u'фото', 'video': u'видео', 'audio': u'аудио', 'document': u'файл',
               'sticker': u'стикер', 'animation': u'гифка'}

VOICE_GAP = 10 * 60         # просьба «напиши текстом» — не чаще раза в 10 минут

_waiting: dict[int, asyncio.Task] = {}
_locks: dict[int, asyncio.Lock] = {}
_voice_told: dict[int, float] = {}
_replies: set[asyncio.Task] = set()


# ------------------------------------------------------------------ факты

def facts() -> str:
    stored = db.get_content(FACTS_KEY)
    return stored[1] if stored and (stored[1] or '').strip() else texts.EVGENY_FACTS


def set_facts(text: str | None) -> None:
    u"""Пустой текст — вернуть стандартные факты."""
    db.put_content(FACTS_KEY, 'evgeny', (text or '').strip())


# ------------------------------------------------------------------ состояние

def is_on(user_id: int) -> bool:
    row = db.evgeny_get(user_id)
    return bool(row and row['active'])


def _mode(user_id: int) -> str:
    u"""coach — ведёт марафон, sales — дожим до спортзала."""
    return (db.evgeny_get(user_id) or {}).get('mode') or 'sales'


def listening(user_id: int) -> bool:
    u"""Отвечает ли Евгений человеку прямо сейчас. Нет ключа ИИ или команда
    выключила координатора — сообщения идут команде обычным путем, с «Получили 🤝»
    (ревью 06.11.2026: иначе человек оставался без ответа вовсе)."""
    row = db.evgeny_get(user_id)
    if not (row and row['active']) or not ai.ready():
        return False
    if config.is_team(user_id):
        return testing(user_id)     # свои говорят с Евгением только в проверке
    # участникам — только когда команда запустила Евгения (/евгений): и координатору,
    # и дожиму по кнопке 🤖 (Sharp 07.11.2026: «клиенты с ним не общались»)
    return coach_enabled()


def daytime(now: float | None = None) -> bool:
    hour = datetime.fromtimestamp(now or time.time(), stats.MSK).hour
    return DAY_HOURS[0] <= hour < DAY_HOURS[1]


def why_not(user_id: int) -> str:
    u"""Почему Евгения нельзя включить этому человеку. Пусто — можно."""
    if not ai.ready():
        return u'нет ключа ИИ — добавьте ANTHROPIC_API_KEY (Claude) в Railway (сервис бота марафона)'
    person = db.get_user(user_id)
    if not person:
        return u'человека нет в базе'
    if config.is_team(user_id):
        return u'это человек из команды'
    if person.get('blocked_at'):
        return u'человек закрыл бота — писать ему нельзя'
    if db.is_banned(user_id):
        return u'человек в чёрном списке'
    return ''


def enable(bot, user_id: int, by_id: int) -> str:
    u"""Включить Евгения из пульта: он сразу пишет. Возвращает причину отказа.
    Кто марафон ещё не прошёл — тому он координатор, прошедшему — дожим.
    Пока команда не запустила Евгения участникам, не включается никому."""
    if not coach_enabled() and not config.is_team(user_id):
        return texts.EVGENY_NOT_LAUNCHED
    wrong = why_not(user_id)
    if wrong:
        return wrong
    db.evgeny_on(user_id, by_id, mode='sales' if db.passed_marathon(user_id) else 'coach')
    asyncio.create_task(_open(bot, user_id))
    log.info(u'Евгений включён у %s (включил %s)', user_id, by_id)
    return ''


def disable(user_id: int, reason: str, kind: str = 'auto') -> bool:
    u"""kind — почему, для бота (db.evgeny_off): после refused и manual
    координатор сам больше не заговорит."""
    task = _waiting.pop(user_id, None)
    if task is not None and not task.done():
        task.cancel()
    done = db.evgeny_off(user_id, reason, kind)
    if done:
        log.info(u'Евгений выключен у %s: %s', user_id, reason)
    return done


def team_wrote(user_id: int, who: str = u'') -> bool:
    u"""Команда ответила человеку сама — Евгений в этом диалоге замолкает,
    а вопрос об оплате, если был, закрыт: звать коллегу уже не нужно."""
    db.evgeny_pay_clear(user_id)
    return disable(user_id, u'ответил(а) сам(а) %s' % who if who else u'команда ответила сама',
                   'team')


# ------------------------------------------------------------------ разговор

def opener(name: str, rng=random) -> str:
    u"""Первое сообщение массового дожима: случайный вариант, с именем или без."""
    text = rng.choice(texts.EVGENY_OPENERS)
    name = (name or '').strip()
    if name:
        return text.replace('{name}', name)
    text = text.replace(u', {name}!', u'!').replace(u'{name}, ', u'')
    return text[:1].upper() + text[1:]


def _card(user_id: int) -> str:
    u"""Что известно о человеке — ИИ знает, с кем говорит."""
    person = db.get_user(user_id) or {}
    days, answers, passed = _progress(user_id)
    lines = [u'Имя: %s' % (person.get('first_name') or u'не указано'),
             u'Откуда пришёл: %s' % stats.source_label(person)]
    said = []
    for day in (1, 2, 3):
        got = answers.get('day%d' % day)
        if got:
            said.append(u'после %d-го дня — «%s»' % (day, u'Да' if got[0] == 'yes' else u'Нет'))
    if said:
        lines.append(u'Ответы в марафоне на вопрос «получилось ли»: ' + u', '.join(said))
    if passed:
        lines.append(u'Марафон пройден до конца (дошёл до 4-го дня).')
    else:
        # Евгения включают из пульта кому угодно — и тем, кто ещё на 1–3 дне
        # (12.10.2026: «а вдруг человеку ещё не всё пришло»)
        lines.append(u'Марафон ещё не пройден до конца: ' + (
            u'получил записи дней %s.' % u', '.join(str(d) for d in days) if days
            else u'записи дней ещё не приходили.'))
    told = sorted(db.feedback_days(user_id))
    lines.append(u'Обратную связь по ощущениям уже давал после дней: %s.'
                 % u', '.join(str(d) for d in told) if told
                 else u'Обратной связи по ощущениям пока не давал.')
    return u'\n'.join(lines)


def _brief(user_id: int) -> str:
    u"""Кто это — одной строкой для команды: откуда пришёл и докуда дошёл."""
    person = db.get_user(user_id) or {}
    days, answers, _passed = _progress(user_id)
    said = [u'после %s-го — %s' % (poll[3:], u'Да' if got[0] == 'yes' else u'Нет')
            for poll, got in sorted(answers.items()) if poll.startswith('day')]
    parts = [u'Пришёл: %s' % stats.source_label(person),
             u'марафон: ' + (u'дни %s' % u', '.join(str(d) for d in days) if days
                             else u'записи дней ещё не приходили')]
    if said:
        parts.append(u'ответы: ' + u', '.join(said))
    return u' · '.join(parts)


def _history(user_id: int) -> list[dict]:
    out = []
    for m in db.chat_history(user_id, HISTORY):
        text = (m.get('text') or '').strip()
        if m['kind'] != 'text':
            what = u'[%s]' % _KIND_NAMES.get(m['kind'], u'вложение')
            text = (what + u' ' + text).strip()
        if not text:
            continue
        out.append({'role': 'user' if m['side'] == 'in' else 'assistant', 'content': text})
    return out


def _request(user_id: int, task: str) -> list[dict]:
    role = texts.EVGENY_COACH_TASK if _mode(user_id) == 'coach' else texts.EVGENY_SALES_TASK
    # Первое системное — одно на всех людей в этом режиме: Claude держит его в кэше
    # (bot/ai.py). Что известно о человеке — отдельным, следом.
    rules = (texts.EVGENY_PROMPT + u'\n\n' + role + u'\n\n' + texts.EVGENY_SENSES
             + u'\n\nФАКТЫ О СПОРТЗАЛЕ\n' + facts())
    history = _history(user_id)
    messages = [{'role': 'system', 'content': rules},
                {'role': 'system', 'content': u'ЧТО ИЗВЕСТНО О ЧЕЛОВЕКЕ\n' + _card(user_id)}] + history
    if not history:                     # переписки нет: шлюзу нужна хоть одна реплика
        messages.append({'role': 'user', 'content': u'(переписки ещё не было)'})
    elif messages[-1]['role'] != 'user':
        # Gemini не берёт запрос, где последняя реплика — бота: «Requests ending
        # with a model turn are not supported» (12.10.2026, у Динара последней
        # была рассылка). Так и в напоминании молчащему — последним писал Евгений.
        messages.append({'role': 'user', 'content': u'(человек пока ничего не ответил)'})
    messages.append({'role': 'system', 'content': task})
    return messages


def parse(raw: str) -> tuple[str, dict]:
    u"""Текст без тегов и сами теги: {'buy': '', 'team': 'вопрос', 'stop': '',
    'pay': 'суть', 'day': [1], 'feedback': [2]}."""
    tags = {}
    for found in _TAG.finditer(raw or ''):
        tags[found.group(1).lower()] = found.group(2).strip(u' :—-')
    for name, tag in (('day', _DAY_TAG), ('feedback', _FEEDBACK_TAG)):
        days = sorted({int(found.group(1)) for found in tag.finditer(raw or '')})
        if days:
            tags[name] = days
    text = _FEEDBACK_TAG.sub('', _DAY_TAG.sub('', _TAG.sub('', raw or '')))
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text, tags


def _typing_seconds(text: str) -> float:
    return min(8.0, max(2.0, len(text) / 18.0))


async def _say(bot, user_id: int, text: str, *, buy: bool = False, mass: bool = False,
               typing: bool = True, strict: bool = False) -> bool:
    u"""Отправить человеку от имени бота и записать в переписку как сообщение Евгения.

    typing — сначала «печатает…»: пауза в несколько секунд. Шагу воронки она не
    годится — планировщик идёт по очереди, и пауза задержала бы всех.
    strict — сбой не глотать: шаг воронки повторит планировщик, как любой другой.
    """
    keys = keyboards.finish('evgeny') if buy else None
    try:
        if typing:
            try:
                await bot.send_chat_action(user_id, 'typing')
                await asyncio.sleep(_typing_seconds(text))
            except Exception:           # «печатает…» — украшение, не повод молчать
                pass
        sent = await delivery._guard(bot.send_message(user_id, text, parse_mode=None,
                                                      reply_markup=keys))
    except delivery.Gone:
        db.mark_blocked(user_id)
        disable(user_id, u'человек закрыл бота', 'gone')
        if strict:
            raise
        return False
    except Exception as err:
        log.warning(u'Евгений не смог написать %s: %s', user_id, err)
        if strict:
            raise
        return False
    # Ушло — значит, ушло: сбой записи не повод слать второй раз (повтор шага).
    try:
        db.save_message(user_id, 'out', 'text', text, None, author=EVGENY,
                        tg_id=getattr(sent, 'message_id', None), mass=mass)
        db.evgeny_touch(user_id, 'out')
    except Exception as err:
        log.warning(u'Евгений: не записали в переписку %s: %s', user_id, err)
    return True


def _who(user_id: int) -> str:
    person = db.get_user(user_id) or {}
    return contact.line(user_id, person.get('first_name'), person.get('username'))


async def _team(bot, user_id: int, text: str) -> None:
    u"""Сообщение команде о человеке; реплай на него — ответ человеку.
    Человек — свой, который проверяет Евгения: уведомление только ему, без
    чёрного списка — остальной команде его проверка не нужна."""
    if config.is_team(user_id):
        # без привязки для реплая: ответ на эту пометку — ответ Евгению (support._relay),
        # а не «команда ответила сама», после которого Евгений замолкает
        try:
            await delivery.note(bot, user_id, texts.EVGENY_TRY_TEAM + text,
                                keyboards.panel_for(user_id))
        except Exception as err:
            log.warning(u'Евгений: проверяющему %s не ушло: %s', user_id, err)
        return
    chats = (((config.SUPPORT_CHAT_ID,) if config.SUPPORT_CHAT_ID else ())
             + config.purchase_recipients())
    for chat in dict.fromkeys(chats):
        try:
            head = await delivery.note(bot, chat, text, keyboards.ban_ask(user_id, chat),
                                       keyboards.ban_ask(user_id))
            db.link_care(chat, head.message_id, user_id)
        except Exception as err:
            log.warning(u'Евгений: команде %s не ушло: %s', chat, err)


async def _turn(bot, user_id: int, task: str) -> bool:
    u"""Спросить ИИ и ответить человеку. True — что-то отправили."""
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if not listening(user_id):
            return False
        try:
            raw = await ai.complete(_request(user_id, task))
        except ai.AIError as err:
            log.warning(u'Евгений: ИИ не ответил для %s: %s', user_id, err)
            await _team(bot, user_id, u'🤖 <b>Евгений не смог ответить</b>\n%s\n%s\n%s' % (
                _who(user_id), html.escape(str(err)), texts.REPLY_HINT))
            return False
        if not listening(user_id):
            # пока ИИ думал, человеку ответила команда — второй ответ следом не нужен
            return False
        text, tags = parse(raw)
        coach_mode = _mode(user_id) == 'coach'
        # координатор кнопку покупки не даёт: об оплате за него говорит команда
        buy = 'buy' in tags and not coach_mode
        if coach_mode and 'pay' in tags:
            text = without_money(text)
        if not text:
            text = texts.EVGENY_FALLBACK if 'team' in tags and 'pay' not in tags else ''
        sent = bool(text) and await _say(bot, user_id, text, buy=buy)
        for day in tags.get('feedback', ()):
            try:
                db.log_event(user_id, 'feedback', str(day))
            except Exception as err:            # отметка — не повод молчать
                log.warning(u'Евгений: не отметили обратную связь %s у %s: %s', day, user_id, err)
        for day in tags.get('day', ()):
            if config.is_team(user_id):
                # проверка: записей дней у своего нет — говорим, что участнику пришло бы
                await _try_note(bot, user_id, texts.EVGENY_TRY_DAY_NOTE.format(day=day))
                continue
            # запись следом за словами Евгения; только уже полученный день (delivery.resend_day_to)
            # и не чаще раза в DAY_RESEND_GAP: модель любит прислать ещё раз, переспрашивая «прислать?»
            mark = 'evgeny:day:%d:%d' % (user_id, day)
            last = db.get_content(mark)
            if last and time.time() - float(last[1] or 0) < DAY_RESEND_GAP:
                continue
            try:
                why = await delivery.resend_day_to(bot, user_id, day)
            except Exception as err:               # не дослали — Евгений всё равно ответил
                why = str(err) or err.__class__.__name__
            if why:
                log.warning(u'Евгений: запись дня %d человеку %s не ушла: %s', day, user_id, why)
            else:
                db.put_content(mark, 'ts', str(time.time()))
        if 'pay' in tags:
            await _pay(bot, user_id, tags['pay'], wait=not buy)
        if 'stop' in tags:
            disable(user_id, u'человек отказался', 'refused')
            await _team(bot, user_id, texts.EVGENY_TEAM_STOP.format(who=_who(user_id)))
        elif 'team' in tags and 'pay' not in tags:
            disable(user_id, u'позвал команду', 'team')
            await _team(bot, user_id, texts.EVGENY_TEAM_ASK.format(
                who=_who(user_id), ask=html.escape(tags['team'] or u'нужен человек из команды')))
        return sent


# ------------------------------------------------------------------ оплата

# Фраза об оплате в ответе координатора. Модель (gpt-4.1-mini, 06.11.2026) и
# с запретом в задаче называла цену и обещала «уточнить у команды», а про оплату
# заказчик решил: говорит команда, не дождался — Евгений зовёт коллегу.
_MONEY = re.compile(u'оплат|плат[её]ж|рассроч|₽|\\bруб|стоимост|\\bцен[аеуыо]|\\d\\s?\\d{3}|'
                    u'команд|коллег', re.I)
_SENTENCE = re.compile(u'(?<=[.!?…])\\s+')


def without_money(text: str) -> str:
    u"""Ответ координатора без фраз об оплате и «передам команде»: про оплату
    за него скажет команда или, не дождавшись её, он сам — «позову коллегу».
    Остался один вопрос («что ещё почувствовал?») — не шлём и его: на вопрос
    о цене такой ответ звучит как уход от ответа."""
    kept = [s for s in _SENTENCE.split(text or '') if s.strip() and not _MONEY.search(s)]
    if all(s.rstrip().endswith('?') for s in kept):
        return ''
    return u' '.join(kept).strip()


def _master(text: str | None) -> bool:
    u"""Речь о записи к мастеру на диагностику, а не об оплате спортзала."""
    return u'диагност' in (text or '').lower()


def _last_in(user_id: int) -> str:
    u"""Последнее, что человек написал, — команде видно, о чём речь."""
    for m in reversed(db.chat_history(user_id, 20)):
        if m['side'] == 'in' and (m.get('text') or '').strip():
            return m['text'].strip()
    return ''


async def _pay(bot, user_id: int, ask: str, *, wait: bool) -> None:
    u"""Человек пишет про оплату (заказчик 06.11.2026): команде — кто это, что
    спросил, и кнопка «ответить в пульте». wait — Евгений про оплату промолчал:
    не напишет команда за PAY_WAIT — он позовёт коллегу (tick → _pay_waits)."""
    row = db.evgeny_get(user_id) or {}
    if (row.get('pay_at') and time.time() - row['pay_at'] < PAY_EPISODE
            and not db.team_wrote_since(user_id, row['pay_at'])):
        return                          # команда уже знает и ещё не ответила — не дублируем
    said = _last_in(user_id)
    # суть — до первой точки: модель, бывает, дописывает после тега вопрос человеку
    ask = re.split(u'(?<=[.!?])\\s', (ask or '').strip())[0].rstrip(u'.').strip()
    # суть модель иногда не пишет — запись к мастеру узнаём и по словам человека
    master = _master(ask) or _master(said)
    ask = ask or (texts.EVGENY_PAY_ASK_MASTER if master else texts.EVGENY_PAY_ASK)
    db.evgeny_pay(user_id, time.time() + PAY_WAIT if wait else None, ask)
    await _team(bot, user_id, texts.EVGENY_TEAM_PAY.format(
        title=texts.EVGENY_PAY_TITLE_MASTER if master else texts.EVGENY_PAY_TITLE,
        who=_who(user_id), card=html.escape(_brief(user_id)), ask=html.escape(ask),
        said=texts.EVGENY_PAY_SAID.format(text=html.escape(said[:300])) if said else u'',
        next=(texts.EVGENY_PAY_WAIT_NOTE.format(minutes=PAY_WAIT // 60) if wait
              else texts.EVGENY_PAY_BUY_NOTE)))


async def _pay_waits(bot, now: float) -> int:
    u"""Про оплату за PAY_WAIT из команды никто не написал: Евгений говорит
    человеку, что зовёт коллегу, и в диалоге замолкает, а команде — «срочно»."""
    done = 0
    for row in db.evgeny_pay_due(now, 20):
        user_id = row['user_id']
        db.evgeny_pay_done(user_id)
        if db.team_wrote_since(user_id, row['pay_at'] or 0):
            continue
        state = texts.EVGENY_PAY_LATE_OFF
        colleague = (texts.EVGENY_PAY_COLLEAGUE_MASTER if _master(row.get('pay_ask'))
                     else texts.EVGENY_PAY_COLLEAGUE)
        # Евгений говорит, только если ещё ведёт диалог (и участникам он запущен);
        # команде «срочно» — в любом случае: человек про оплату ждёт ответа
        if listening(user_id) and await _say(bot, user_id, colleague, typing=False):
            disable(user_id, u'позвал коллегу про оплату', 'team')
            state = texts.EVGENY_PAY_LATE_CALLED
        await _team(bot, user_id, texts.EVGENY_TEAM_PAY_LATE.format(
            who=_who(user_id), ask=html.escape(row.get('pay_ask') or u'пишет про оплату'),
            minutes=max(1, int((now - (row['pay_at'] or now)) // 60)), state=state))
        done += 1
    return done


# ------------------------------------------------------------------ координатор марафона

def coach_enabled() -> bool:
    u"""Команда включила координатора участникам (/евгений). Не включала — выключен:
    Sharp 07.11.2026 — сначала команда проверяет Евгения на себе, потом запускает."""
    stored = db.get_content(COACH_KEY)
    return bool(stored and stored[1] == 'on')


def coach_on() -> bool:
    u"""Координатор работает у участников: включён и есть ключ ИИ — без ИИ отвечать некому."""
    return ai.ready() and coach_enabled()


def coach_for(user_id: int) -> bool:
    u"""Пишет ли координатор этому человеку по сценарию: участнику — когда его
    включила команда, своему — всегда: так команда проверяет Евгения (/test)."""
    return ai.ready() and (coach_enabled() or config.is_team(user_id))


def set_coach(on: bool) -> None:
    db.put_content(COACH_KEY, 'evgeny', 'on' if on else 'off')
    log.info(u'Евгений-координатор %s', u'включён' if on else u'выключен')


def coach_text(user_id: int, ref: str) -> str:
    u"""Что написать по сценарию (ref — intro, day1…day4). Пусто — писать нечего:
    уже знакомились или об ощущениях этого дня человек рассказал сам."""
    wrote = db.wrote_as(user_id, EVGENY)
    if ref == 'intro':
        return '' if wrote else texts.EVGENY_COACH_INTRO
    day = int(ref[3:])
    told = db.feedback_days(user_id)
    if day in told:
        return ''
    before = [d for d in told if d < day]
    if before:
        text = texts.EVGENY_COACH_AGAIN.format(after=texts.EVGENY_COACH_AFTER[max(before)],
                                               sphere=texts.EVGENY_COACH_SPHERE[day])
    else:
        text = texts.EVGENY_COACH_ASK.format(day=texts.EVGENY_COACH_DAY[day])
    return text if wrote else texts.EVGENY_COACH_HELLO + text


def _coach_may(user_id: int) -> bool:
    u"""Можно ли координатору писать: бот не закрыт, человек не в чёрном списке,
    не просил не писать, и команда не выключала ему Евгения сама."""
    person = db.get_user(user_id)
    if not person or person.get('blocked_at') or db.is_banned(user_id):
        return False
    row = db.evgeny_get(user_id)
    return not row or bool(row['active']) or row.get('off_kind') not in HOLD


def _listen(user_id: int) -> None:
    u"""Координатор спросил — он и ответит. Где команда сама недавно писала
    человеку, в разговор не встревает: ответ человека увидит она. Своему в
    прогоне /test отвечает всегда — это проверка (try_start)."""
    if config.is_team(user_id):
        if not testing(user_id):
            db.evgeny_on(user_id, user_id, mode='coach')
        return
    row = db.evgeny_get(user_id)
    if row and row['active']:
        return
    if row and row.get('mode') != 'coach':
        return                      # дожим команда завершила сама — координатор его не оживляет
    if db.team_wrote_since(user_id, time.time() - TEAM_QUIET):
        return
    db.evgeny_coach_on(user_id)


async def coach(bot, user_id: int, ref: str) -> None:
    u"""Сообщение координатора по сценарию — шаг воронки evgeny.

    Сбой отправки — исключением, как у любого шага: планировщик повторит, а
    закрывшего бота снимет с марафона. Участникам — только когда координатора
    включила команда; своим — всегда: прогон /test показывает и Евгения."""
    if not coach_for(user_id) or not _coach_may(user_id):
        log.info(u'Евгений-координатор: %s для %s пропущено (выключен, нет ключа ИИ или '
                 u'человеку писать нельзя)', ref, user_id)
        return
    text = coach_text(user_id, ref)
    if text and await _say(bot, user_id, text, typing=False, strict=True):
        _listen(user_id)
        log.info(u'Евгений-координатор → %s: %s', user_id, ref)


# ------------------------------------------------------------------ проверка командой

def testing(user_id: int) -> bool:
    u"""Свой проверяет Евгения: разговор включён и не заброшен дольше TRY_IDLE."""
    if not config.is_team(user_id):
        return False
    row = db.evgeny_get(user_id)
    if not (row and row['active']):
        return False
    last = max(row.get('since') or 0, row.get('last_in') or 0, row.get('last_out') or 0)
    return time.time() - last < TRY_IDLE


def _stage(user_id: int) -> int | None:
    u"""Про какой день Евгений спросил своего в проверке (0 — только познакомился).
    None — не проверка кнопкой: участник или свой в прогоне /test."""
    if not config.is_team(user_id):
        return None
    stored = db.get_content(TRY_KEY % user_id)
    value = (stored[1] if stored else '') or ''
    return int(value) if value.isdigit() else None


def _progress(user_id: int) -> tuple[list[int], dict, bool]:
    u"""Полученные дни, ответы под днями и пройден ли марафон — для карточки ИИ.
    У своего в проверке — как у участника в ту минуту, когда Евгений пишет ему
    по сценарию: знакомство — после первой записи, вопрос про день N — после
    записи N+1 тому, кто под днём N нажал «Да» (funnel.EVGENY_AFTER_DAY)."""
    stage = _stage(user_id)
    if stage is None:
        return sorted(db.days_of(user_id)), db.answers_of(user_id), db.passed_marathon(user_id)
    days = list(range(1, min(stage + 1, 4) + 1))
    answers = {'day%d' % d: ('yes', None) for d in range(1, min(stage, 3) + 1)}
    return days, answers, stage >= 3


async def _try_note(bot, user_id: int, text: str) -> None:
    u"""Пометка проверяющему от бота — не реплика Евгения, в переписку не пишем."""
    try:
        await bot.send_message(user_id, text)
    except Exception as err:
        log.warning(u'Евгений: пометка проверки %s не ушла: %s', user_id, err)


def _forget(user_id: int) -> None:
    u"""Стереть разговор с Евгением — только своему: переписку участника не трогаем."""
    if not config.is_team(user_id):
        return
    task = _waiting.pop(user_id, None)
    if task is not None and not task.done():
        task.cancel()
    _voice_told.pop(user_id, None)
    db.evgeny_forget(user_id)


async def try_start(user_id: int, username: str | None, first_name: str | None) -> str:
    u"""Кнопка «🤖 Проверить Евгения»: Евгений начинает со своим заново — как с
    участником, которому только что пришёл первый день. Прошлая проверка
    стирается. Возвращает, почему нельзя (пусто — можно).

    Под тем же замком, что и ответ ИИ (_turn): ответ, который Евгений уже
    готовит, уходит до стирания, а не после нового знакомства."""
    if not config.is_team(user_id):
        return texts.EVGENY_TRY_TEAM_ONLY
    if not ai.ready():
        return texts.EVGENY_NO_KEY
    async with _locks.setdefault(user_id, asyncio.Lock()):
        db.remember_user(user_id, username, first_name)
        _forget(user_id)
        db.put_content(TRY_KEY % user_id, 'evgeny', '0')
        db.evgeny_on(user_id, user_id, mode='coach')
    log.info(u'Евгений: проверка командой — %s начал(а) заново', user_id)
    return ''


async def try_intro(bot, user_id: int) -> bool:
    u"""Первое сообщение проверки — то же знакомство, что получает участник."""
    return await _say(bot, user_id, coach_text(user_id, 'intro') or texts.EVGENY_COACH_INTRO)


async def try_day(bot, user_id: int, day: int) -> str:
    u"""Кнопка «N-й день»: свой как будто посмотрел день и нажал «Да» — Евгений
    спрашивает про ощущения теми же словами, что участника, и так же молчит,
    если об этом дне уже рассказали. Пусто — спросил, иначе — почему нет."""
    if _stage(user_id) is None:
        return texts.EVGENY_TRY_NOT_STARTED
    if not ai.ready():
        return texts.EVGENY_NO_KEY
    if not _coach_may(user_id):
        return texts.EVGENY_TRY_HELD
    db.put_content(TRY_KEY % user_id, 'evgeny', str(day))
    text = coach_text(user_id, 'day%d' % day)
    if not text:
        return texts.EVGENY_TRY_TOLD.format(day=day)
    if not await _say(bot, user_id, text, typing=False):
        return texts.EVGENY_TRY_FAILED
    _listen(user_id)
    return ''


def try_end(user_id: int) -> None:
    u"""Закончить проверку: свой снова пишет боту как команда, а не участник."""
    db.put_content(TRY_KEY % user_id, 'evgeny', '')
    disable(user_id, u'проверка закончена', 'manual')


def try_reset(user_id: int) -> None:
    u"""Прогон /test начинается заново — и разговор с Евгением: знакомство придёт снова."""
    if config.is_team(user_id):
        _forget(user_id)
        db.put_content(TRY_KEY % user_id, 'evgeny', '')


async def _open(bot, user_id: int) -> None:
    u"""Евгения включили из пульта: если человек ждёт ответа — отвечает,
    иначе начинает разговор сам."""
    try:
        history = db.chat_history(user_id, HISTORY)
        if any(m.get('author') == EVGENY for m in history):
            # уже писал — продолжает, а не здоровается заново (AleX 19.10.2026)
            task = texts.EVGENY_CONTINUE_TASK
        elif history and history[-1]['side'] == 'in':
            task = texts.EVGENY_REPLY_TASK
        else:
            task = texts.EVGENY_OPEN_TASK
        await _turn(bot, user_id, task)
    except Exception:
        log.exception(u'Евгений: не смог начать диалог с %s', user_id)


def heard(bot, message) -> bool:
    u"""Человек написал боту, а Евгений ведёт его диалог. True — Евгений
    ответит (сообщение уже в переписке и у команды). Голосовое ИИ не
    услышит — просим написать текстом."""
    user_id = message.from_user.id
    if not listening(user_id):
        return False
    db.evgeny_touch(user_id, 'in')
    if getattr(message, 'voice', None) or getattr(message, 'video_note', None):
        # на серию голосовых — одна просьба, а не по одной на каждое
        if time.time() - _voice_told.get(user_id, 0) > VOICE_GAP:
            _voice_told[user_id] = time.time()
            voice = texts.EVGENY_VOICE_TY if _mode(user_id) == 'coach' else texts.EVGENY_VOICE
            task = asyncio.create_task(_say(bot, user_id, voice))
            _replies.add(task)                  # ссылка — чтобы задачу не собрал сборщик
            task.add_done_callback(_replies.discard)
        return True
    if not (message.text or message.caption):
        return True                     # стикер или файл без слов — увидит команда
    old = _waiting.pop(user_id, None)
    if old is not None and not old.done():
        old.cancel()
    _waiting[user_id] = asyncio.create_task(_later(bot, user_id))
    return True


async def _later(bot, user_id: int) -> None:
    try:
        await asyncio.sleep(DEBOUNCE)
    except asyncio.CancelledError:
        return
    _waiting.pop(user_id, None)
    task = texts.EVGENY_COACH_REPLY_TASK if _mode(user_id) == 'coach' else texts.EVGENY_REPLY_TASK
    try:
        await _turn(bot, user_id, task)
    except Exception:
        log.exception(u'Евгений: ответ %s упал', user_id)


# ------------------------------------------------------------------ массовый дожим

def candidates(now: float | None = None) -> list[dict]:
    rows = db.evgeny_candidates((now or time.time()) - DAY4_WAIT)
    return [r for r in rows if not config.is_team(r['user_id'])]


def push(by_id: int) -> int:
    u"""Поставить в очередь всех, кто прошёл марафон и ничего не сделал."""
    found = candidates()
    for person in found:
        db.evgeny_on(person['user_id'], by_id, pending=True)
    log.info(u'Евгений: массовый дожим — %d чел. (запустил %s)', len(found), by_id)
    return len(found)


def stop_all(reason: str) -> int:
    u"""Выключить Евгения у всех — и координатора: «у всех» значит и у новых."""
    for task in list(_waiting.values()):
        task.cancel()
    _waiting.clear()
    set_coach(False)
    return db.evgeny_off_all(reason)


async def _openers(bot, limit: int) -> int:
    done = 0
    for user_id in db.evgeny_pending(limit):
        if why_not(user_id):
            disable(user_id, why_not(user_id), 'gone')
            continue
        person = db.get_user(user_id) or {}
        if db.wrote_as(user_id, EVGENY):
            # вёл его марафон координатором — продолжает тот разговор, а не здоровается
            sent = await _turn(bot, user_id, texts.EVGENY_PUSH_TASK)
        else:
            sent = await _say(bot, user_id, opener(person.get('first_name') or ''), mass=True)
        if sent:
            done += 1
        else:
            db.evgeny_off(user_id, u'первое сообщение не ушло')
        await asyncio.sleep(60.0 / OPEN_PER_MINUTE)
    return done


async def _nudges(bot, limit: int) -> int:
    done = 0
    for row in db.evgeny_silent(time.time() - NUDGE_AFTER, MAX_NUDGES, limit):
        user_id = row['user_id']
        if why_not(user_id):
            disable(user_id, why_not(user_id), 'gone')
            continue
        if await _turn(bot, user_id, texts.EVGENY_NUDGE_TASK.format(n=row['nudges'] + 1)):
            db.evgeny_nudged(user_id)
            done += 1
        else:
            db.evgeny_nudged(user_id)   # не вышло — не долбим каждые полминуты
    return done


async def tick(bot, now: float | None = None) -> None:
    u"""Один проход: первые сообщения из очереди и напоминания молчащим —
    только днём, только когда есть ключ ИИ и Евгений запущен участникам."""
    if not ai.ready() or not coach_enabled() or not daytime(now):
        return
    await _openers(bot, max(1, int(OPEN_PER_MINUTE * TICK / 60)))
    await _nudges(bot, 5)


async def pay_tick(bot, now: float | None = None) -> int:
    u"""Про оплату — как только вышел срок, в любое время суток."""
    return await _pay_waits(bot, now or time.time())


async def _every(name: str, step, bot) -> None:
    while True:
        try:
            await step(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(u'Евгений: проход «%s» упал', name)
        await asyncio.sleep(TICK)


async def loop(bot) -> None:
    u"""Два цикла рядом: оплата не ждёт, пока уйдут первые сообщения дожима
    (ревью 06.11.2026: обещанные 5 минут иначе растягивались на 6–8)."""
    log.info(u'Евгений: циклы дожима и оплаты запущены')
    await asyncio.gather(_every(u'дожим', tick, bot), _every(u'оплата', pay_tick, bot))
