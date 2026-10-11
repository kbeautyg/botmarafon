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
TICK = 30                   # как часто проверять очередь и молчащих
NUDGE_AFTER = 24 * 3600     # напоминание молчащему — через сутки
MAX_NUDGES = 2
OPEN_PER_MINUTE = 20        # массовый дожим: первых сообщений в минуту
DAY4_WAIT = 72 * 3600       # «после марафона»: с 4-го дня прошло 72 часа (записи закрыты)
DAY_HOURS = (9, 21)         # писать первым — только с 9 до 21 по Москве
FACTS_KEY = 'evgeny:facts'

_TAG = re.compile(r'\[(BUY|TEAM|STOP)\]([^\n\[]*)', re.I)
_KIND_NAMES = {'voice': u'голосовое сообщение', 'video_note': u'видео-кружок',
               'photo': u'фото', 'video': u'видео', 'audio': u'аудио', 'document': u'файл',
               'sticker': u'стикер', 'animation': u'гифка'}

_waiting: dict[int, asyncio.Task] = {}
_locks: dict[int, asyncio.Lock] = {}


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


def daytime(now: float | None = None) -> bool:
    hour = datetime.fromtimestamp(now or time.time(), stats.MSK).hour
    return DAY_HOURS[0] <= hour < DAY_HOURS[1]


def why_not(user_id: int) -> str:
    u"""Почему Евгения нельзя включить этому человеку. Пусто — можно."""
    if not ai.ready():
        return u'нет ключа ИИ — добавьте AI_API_KEY в Railway (сервис бота марафона)'
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
    u"""Включить Евгения из пульта: он сразу пишет. Возвращает причину отказа."""
    wrong = why_not(user_id)
    if wrong:
        return wrong
    db.evgeny_on(user_id, by_id)
    asyncio.create_task(_open(bot, user_id))
    log.info(u'Евгений включён у %s (включил %s)', user_id, by_id)
    return ''


def disable(user_id: int, reason: str) -> bool:
    task = _waiting.pop(user_id, None)
    if task is not None and not task.done():
        task.cancel()
    done = db.evgeny_off(user_id, reason)
    if done:
        log.info(u'Евгений выключен у %s: %s', user_id, reason)
    return done


def team_wrote(user_id: int, who: str = u'') -> bool:
    u"""Команда ответила человеку сама — Евгений в этом диалоге замолкает."""
    return disable(user_id, u'ответил(а) сам(а) %s' % who if who else u'команда ответила сама')


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
    answers = db.answers_of(user_id)
    lines = [u'Имя: %s' % (person.get('first_name') or u'не указано'),
             u'Откуда пришёл: %s' % stats.source_label(person)]
    said = []
    for day in (1, 2, 3):
        got = answers.get('day%d' % day)
        if got:
            said.append(u'после %d-го дня — «%s»' % (day, u'Да' if got[0] == 'yes' else u'Нет'))
    if said:
        lines.append(u'Ответы в марафоне на вопрос «получилось ли»: ' + u', '.join(said))
    if db.passed_marathon(user_id):
        lines.append(u'Марафон прошёл до 4-го дня.')
    return u'\n'.join(lines)


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
    system = (texts.EVGENY_PROMPT + u'\n\nФАКТЫ О СПОРТЗАЛЕ\n' + facts()
              + u'\n\nЧТО ИЗВЕСТНО О ЧЕЛОВЕКЕ\n' + _card(user_id))
    messages = [{'role': 'system', 'content': system}] + _history(user_id)
    if len(messages) == 1:              # переписки нет: шлюзу нужна хоть одна реплика
        messages.append({'role': 'user', 'content': u'(переписки ещё не было)'})
    messages.append({'role': 'system', 'content': task})
    return messages


def parse(raw: str) -> tuple[str, dict]:
    u"""Текст без тегов и сами теги: {'buy': '', 'team': 'вопрос', 'stop': ''}."""
    tags = {}
    for found in _TAG.finditer(raw or ''):
        tags[found.group(1).lower()] = found.group(2).strip(u' :—-')
    text = _TAG.sub('', raw or '')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text, tags


def _typing_seconds(text: str) -> float:
    return min(8.0, max(2.0, len(text) / 18.0))


async def _say(bot, user_id: int, text: str, *, buy: bool = False, mass: bool = False) -> bool:
    u"""Отправить человеку от имени бота и записать в переписку как сообщение Евгения."""
    keys = keyboards.finish('evgeny') if buy else None
    try:
        try:
            await bot.send_chat_action(user_id, 'typing')
            await asyncio.sleep(_typing_seconds(text))
        except Exception:               # «печатает…» — украшение, не повод молчать
            pass
        sent = await delivery._guard(bot.send_message(user_id, text, parse_mode=None,
                                                      reply_markup=keys))
    except delivery.Gone:
        db.mark_blocked(user_id)
        disable(user_id, u'человек закрыл бота')
        return False
    except Exception as err:
        log.warning(u'Евгений не смог написать %s: %s', user_id, err)
        return False
    db.save_message(user_id, 'out', 'text', text, None, author=EVGENY,
                    tg_id=getattr(sent, 'message_id', None), mass=mass)
    db.evgeny_touch(user_id, 'out')
    return True


def _who(user_id: int) -> str:
    person = db.get_user(user_id) or {}
    return contact.line(user_id, person.get('first_name'), person.get('username'))


async def _team(bot, user_id: int, text: str) -> None:
    u"""Сообщение команде о человеке; реплай на него — ответ человеку."""
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
        if not is_on(user_id):
            return False
        try:
            raw = await ai.complete(_request(user_id, task))
        except ai.AIError as err:
            log.warning(u'Евгений: ИИ не ответил для %s: %s', user_id, err)
            await _team(bot, user_id, u'🤖 <b>Евгений не смог ответить</b>\n%s\n%s\n%s' % (
                _who(user_id), html.escape(str(err)), texts.REPLY_HINT))
            return False
        text, tags = parse(raw)
        if not text:
            text = texts.EVGENY_FALLBACK if 'team' in tags else ''
        sent = bool(text) and await _say(bot, user_id, text, buy='buy' in tags)
        if 'stop' in tags:
            disable(user_id, u'человек отказался')
            await _team(bot, user_id, texts.EVGENY_TEAM_STOP.format(who=_who(user_id)))
        elif 'team' in tags:
            disable(user_id, u'позвал команду')
            await _team(bot, user_id, texts.EVGENY_TEAM_ASK.format(
                who=_who(user_id), ask=html.escape(tags['team'] or u'нужен человек из команды')))
        return sent


async def _open(bot, user_id: int) -> None:
    u"""Евгения включили из пульта: если человек ждёт ответа — отвечает,
    иначе начинает разговор сам."""
    try:
        history = db.chat_history(user_id, 1)
        waiting = bool(history) and history[-1]['side'] == 'in'
        await _turn(bot, user_id, texts.EVGENY_REPLY_TASK if waiting else texts.EVGENY_OPEN_TASK)
    except Exception:
        log.exception(u'Евгений: не смог начать диалог с %s', user_id)


def heard(bot, message) -> bool:
    u"""Человек написал боту, а Евгений ведёт его диалог. True — Евгений
    ответит (сообщение уже в переписке и у команды). Голосовое ИИ не
    услышит — просим написать текстом."""
    user_id = message.from_user.id
    if not is_on(user_id):
        return False
    db.evgeny_touch(user_id, 'in')
    if getattr(message, 'voice', None) or getattr(message, 'video_note', None):
        asyncio.create_task(_say(bot, user_id, texts.EVGENY_VOICE))
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
    try:
        await _turn(bot, user_id, texts.EVGENY_REPLY_TASK)
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
    for task in list(_waiting.values()):
        task.cancel()
    _waiting.clear()
    return db.evgeny_off_all(reason)


async def _openers(bot, limit: int) -> int:
    done = 0
    for user_id in db.evgeny_pending(limit):
        if why_not(user_id):
            disable(user_id, why_not(user_id))
            continue
        person = db.get_user(user_id) or {}
        if await _say(bot, user_id, opener(person.get('first_name') or ''), mass=True):
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
            disable(user_id, why_not(user_id))
            continue
        if await _turn(bot, user_id, texts.EVGENY_NUDGE_TASK.format(n=row['nudges'] + 1)):
            db.evgeny_nudged(user_id)
            done += 1
        else:
            db.evgeny_nudged(user_id)   # не вышло — не долбим каждые полминуты
    return done


async def tick(bot, now: float | None = None) -> None:
    u"""Один проход: первые сообщения из очереди и напоминания молчащим —
    только днём и только когда есть ключ ИИ."""
    if not ai.ready() or not daytime(now):
        return
    await _openers(bot, max(1, int(OPEN_PER_MINUTE * TICK / 60)))
    await _nudges(bot, 5)


async def loop(bot) -> None:
    log.info(u'Евгений: цикл дожима запущен')
    while True:
        try:
            await tick(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(u'Евгений: проход упал')
        await asyncio.sleep(TICK)
