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

Правки после проверки Павла (09.11.2026): Евгений на «вы»; предложит человек сам
«давай на ты» — переходит и запоминает (on_ty: слова человека или тег [TY]).
Чтобы ответы не шли под копирку, ИИ видит, как начинались и заканчивались его
последние сообщения этому человеку (_echo). Врач — только при неотложном
состоянии (texts.EVGENY_PROMPT), а не в каждой обратной связи.

11.11.2026: на «ты» — и когда человек сам перешёл (тег [TY]); от дня к дню —
после 2-го пример, после 3-го пример и мягкая рекомендация спортзала, после 4-го
предложение, согласие — [PAY] команде («Хочет в спортзал»). Примеры — реальные
истории от команды (cases, /евгений_примеры) или обобщённо: людей не придумывает.

После проверки Павла 11.11.2026 вечером: по имени не обращается — имени человека ИИ не
видит; программа Павла — «Энергоспортзал», не «спортзал» (energo — страховка на случай,
если ИИ всё же напишет по-старому); «хорошо, посмотрю», «спасибо», «пока нет вопросов» —
ответ одной короткой фразой без вопроса, а на второе «спасибо» подряд — тишина (closing).

Павел 11.11.2026, ночью (голосовые): прощаясь после предложения Энергоспортзала (отказ, «подумаю»,
«пока нет вопросов»), Евгений шлёт следом два эфира Павла — его текстом слово в слово, подряд,
через полминуты каждый (_efir), и затихает. Сутки тишины — спрашивает, как эфиры (_efir_asks), а
дальше снова говорит с человеком и потихоньку подводит к Энергоспортзалу (EVGENY_EFIR_TALK).

Павел 12.11.2026 (проверка): Евгений спросил «слева или справа?», ответ пришёл через час — и весь
час Евгений молчал: пока идёт марафон, напоминаний нет, следующее его сообщение — вопрос про
следующий день. Теперь спросил в разговоре о дне, а человек SENDOFF_AFTER молчит — Евгений один
раз за день желает смотреть следующий день и прислушиваться к телу (_sendoffs).

Павел 12.11.2026, вечер: про эфиры — через 15 часов тишины «Смотрели вы эфиры, которые я прислал?»
и, исходя из разговора, предложение попробовать месяц в Энергоспортзале — потрансформировать все
сферы жизни (EVGENY_EFIR_TALK). Эфиры — только на отказ после уже прозвучавшего предложения:
«финансов нету» в рассказе о 4-м дне отказом больше не считается (_offered, _farewell). И в
проверке вопрос об эфирах — так же через 15 часов и днём: через 2 минуты он «лишний».

Павел 14.11.2026 (голосовые после проверки): пример — после обратной связи, а не в начале («Марина
тоже чувствовала…» путает), словами «И, кстати, хочу рассказать про…», на 3-м и 4-м дне — подробно;
ответы про эгрегоры и «можно ли нахватать на себя» — в фактах, а не «уточню у команды». В примере —
только то, что в истории: срок не придумывать, разные истории с одним именем не склеивать.
"""
import asyncio
import html
import logging
import random
import re
import time
from datetime import datetime
from typing import NamedTuple

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
CASES_KEY = 'evgeny:cases'  # реальные истории участников для примеров (/евгений_примеры)
COACH_KEY = 'evgeny:coach'  # 'on' — команда включила координатора участникам (/евгений)
TRY_KEY = 'evgeny:try:%d'   # проверка командой: про какой день Евгений спросил своего (0 — знакомство)
# Проверку забросили — свой снова пишет боту как команда. Не сутки, а 30 часов: вопрос об эфирах
# и в проверке — через 15 часов тишины и только днём, то есть до 27 часов (Павел 12.11.2026).
TRY_IDLE = 30 * 3600
TY_KEY = 'evgeny:ty:%d'     # '1' — человек сам предложил перейти на «ты» (стирает db.evgeny_forget)
CLOSED_KEY = 'evgeny:closed:%d'  # когда Евгений ответил на «спасибо» и «хорошо, посмотрю» (closing)
CLOSING_LEN = 120           # длиннее — человек пишет о деле, а не прощается
CLOSING_MAX = 160           # ответ на «хорошо, посмотрю» — не длиннее
EFIR_GAP = 30.0             # эфиры Павла после прощания — по одному через полминуты
EFIR_QUIET = 15 * 3600      # 15 часов тишины после эфиров — Евгений спрашивает, смотрели ли (Павел 12.11)
EFIR_LATEST = 72 * 3600     # позже о них не спрашиваем: записи марафона давно закрыты
SENDOFF_AFTER = 10 * 60     # молчит на вопрос о дне 10 минут — пожелание смотреть следующий день
SENDOFF_FRESH = 3 * 3600    # разговор о дне — пока рассказ об ощущениях свежий; позже не провожаем
ECHO = 4                    # столько своих последних сообщений Евгений видит, чтобы не повторяться
ASK_BY_AI = True            # вопрос про день словами ИИ; False — всегда текст заказчика
ASK_TIMEOUT = 20.0          # столько ждём ИИ для вопроса про день — дальше уходит текст заказчика
ASK_MAX = 400               # длиннее — ИИ ушёл не туда, вопрос уходит текстом заказчика
PAY_WAIT = 5 * 60           # столько ждём, что про оплату человеку напишет команда
PAY_EPISODE = 3 * 3600      # то же оплатное дело: команде второй раз не пишем
TEAM_QUIET = 6 * 3600       # команда недавно писала человеку — координатор не встревает
# Так выключенного Евгения координатор сам не включает: человек просил не писать,
# команда выключила вручную.
HOLD = ('refused', 'manual')

_TAG = re.compile(r'\[(BUY|TEAM|STOP|PAY|TY|BYE)\]([^\n\[]*)', re.I)
# «Давай на ты», «можно на ты?», «пишите мне на «ты»», «не надо на вы» — человек сам
# предлагает перейти на «ты». Чего здесь нет («мы же можем на ты»), узнаёт ИИ: тег [TY].
_TY_WORDS = u'(?:со\\s+мной\\s+|ко\\s+мне\\s+|мне\\s+|уже\\s+)?на\\s+[«"„\']?%s\\b'
_OFFER_TY = re.compile(
    u'\\b(?:давай(?:те)?|можно|лучше|пиши(?:те)?|обращайся|обращайтесь|общайся|общайтесь|'
    u'перейд[её]м|перейти|переходи(?:те)?)\\s+' + _TY_WORDS % u'ты'
    + u'|\\bне\\s+(?:надо|нужно|стоит)\\s+' + _TY_WORDS % u'вы', re.I)
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


def cases() -> str:
    u"""Реальные истории участников, которые дала команда (заказчик 11.11.2026: «надо,
    чтобы он с примером писал»). Своих по умолчанию нет: выдуманный человек с
    «настоящим» именем — фальшивый отзыв, поэтому пусто — Евгений говорит обобщённо."""
    stored = db.get_content(CASES_KEY)
    return (stored[1] or '').strip() if stored else ''


def set_cases(text: str | None, *, add: bool = False) -> str:
    u"""Заменить истории (add — дописать к имеющимся). Пустой текст — очистить."""
    text = (text or '').strip()
    if add and text and cases():
        text = cases() + u'\n\n' + text
    db.put_content(CASES_KEY, 'evgeny', text)
    return text


# ------------------------------------------------------------------ состояние

def is_on(user_id: int) -> bool:
    row = db.evgeny_get(user_id)
    return bool(row and row['active'])


def _mode(user_id: int) -> str:
    u"""coach — ведёт марафон, sales — дожим до спортзала."""
    return (db.evgeny_get(user_id) or {}).get('mode') or 'sales'


def on_ty(user_id: int) -> bool:
    u"""Евгений с человеком на «ты»: тот сам предложил. Иначе — на «вы» (AleX 09.11.2026)."""
    stored = db.get_content(TY_KEY % user_id)
    return bool(stored and stored[1] == '1')


def offers_ty(text: str | None) -> bool:
    u"""Человек сам предлагает перейти на «ты»."""
    return bool(_OFFER_TY.search(text or ''))


def _set_ty(user_id: int) -> None:
    if not on_ty(user_id):
        db.put_content(TY_KEY % user_id, 'evgeny', '1')
        log.info(u'Евгений: с %s теперь на «ты» — человек предложил сам', user_id)


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

def opener(rng=random) -> str:
    u"""Первое сообщение массового дожима: случайный вариант, без имени (Sharp 11.11.2026:
    «он не должен обращаться по имени»)."""
    return rng.choice(texts.EVGENY_OPENERS)


def _card(user_id: int) -> str:
    u"""Что известно о человеке — ИИ знает, с кем говорит. Имени нет: Sharp 11.11.2026 —
    «он не должен видеть имя пользователя» (тестовый аккаунт «Евгений» Евгений назвал
    Евгением)."""
    person = db.get_user(user_id) or {}
    days, answers, passed = _progress(user_id)
    lines = [u'Откуда пришёл: %s' % stats.source_label(person)]
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
    lines.append(u'Обращение: на «ты» — человек сам на него перешёл.' if on_ty(user_id)
                 else u'Обращение: на «вы» — на «ты» человек не переходил.')
    return u'\n'.join(lines)


def _head(sentence: str, size: int = 90) -> str:
    sentence = sentence.strip()
    return sentence if len(sentence) <= size else sentence[:size].rsplit(' ', 1)[0] + u'…'


def _tail(sentence: str, size: int = 110) -> str:
    sentence = sentence.strip()
    return sentence if len(sentence) <= size else u'…' + sentence[-size:].split(' ', 1)[-1]


# Слова метода, которые Евгений повторял из ответа в ответ (Павел 09.11.2026: «на все
# дни однотипно про тонкий план, родовую систему и застои»): ИИ видит, сколько раз они
# уже звучали, и говорит проще.
_TERMS = ((u'«тонкий план»', re.compile(u'тонк\\w*\\s+план', re.I)),
          (u'«родовая система»', re.compile(u'родов\\w*\\s+систем', re.I)),
          (u'«линия рода»', re.compile(u'лини\\w*\\s+рода', re.I)),
          (u'«застои»', re.compile(u'засто', re.I)),
          (u'«низкочастотные энергии»', re.compile(u'низкочастотн|низк\\w*\\s+частот', re.I)),
          (u'«отклик»', re.compile(u'отклик', re.I)),
          (u'«зажим»', re.compile(u'зажим', re.I)))

# Чем начать, чем закончить и каким ритмом — по жребию (Sharp 11.11.2026: «намного
# разнообразнее… видно, что он однообразный»). Одни и те же правила дают модели одну и ту
# же форму ответа; жребий меняет её от сообщения к сообщению и от человека к человеку.
_OPENINGS = (
    u'начни прямо со смысла самого яркого ощущения, без вводных слов',
    u'начни с короткой живой реакции на его слова — не «Сильный отклик!» и не «Благодарю»',
    u'начни с его собственного слова или образа из сообщения — повтори его и объясни, что за ним '
    u'стоит',
    u'начни с того, что объединяет его ощущения, а потом коротко про главное',
    # «начни с истории другого участника» убрано (Павел 14.11.2026): «Марина тоже чувствовала…» в
    # начале путает — человек не понимает, о ком речь; пример — только после обратной связи
    u'начни с главного вывода о его ощущениях одной фразой, а потом — подробности',
    u'начни с простой поддержки своими словами: так бывает, и это работа, а не сбой',
)
_CLOSINGS = (
    u'закончи тёплой фразой без вопроса',
    u'закончи коротким вопросом по сути его ощущений — если он о них сейчас рассказал, иначе без '
    u'вопроса',
    u'закончи пожеланием к следующему дню, без вопроса',
    u'закончи совсем коротко — в 3–6 слов',
    u'закончи мыслью о том, что дальше будет глубже',
)
_RHYTHMS = (
    u'предложения короткие, разговорные',
    u'одно предложение подлиннее, остальные короткие',
    u'одна из фраз совсем короткая, в два-три слова',
    u'мысли связывай союзами, как в живой речи, а не перечнем через тире',
)


def _style(rng=random) -> str:
    u"""Как написать этот ответ — жребий для разнообразия; правила этапа важнее."""
    return (u'КАК НАПИСАТЬ ЭТОТ ОТВЕТ (жребий, чтобы ответы не были похожи один на другой): %s; '
            u'%s; %s. Если правила «ОТ ДНЯ К ДНЮ» или задача требуют другого — они важнее.'
            % (rng.choice(_OPENINGS), rng.choice(_CLOSINGS), rng.choice(_RHYTHMS)))


def _echo(user_id: int) -> str:
    u"""Как начинались и заканчивались последние сообщения Евгения этому человеку и
    какие слова метода в них уже звучали — чтобы следующее он написал по-другому.
    Павел 09.11.2026: «на все дни однотипно рассказывает»; AleX: «„какие будут
    ощущения, хорошо?“ через один день»."""
    mine = [(m.get('text') or '').strip() for m in db.chat_history(user_id, HISTORY)
            if m.get('author') == EVGENY and m['side'] == 'out' and (m.get('text') or '').strip()
            and not _attachment(m)]
    if not mine:
        return ''
    heads, tails = [], []
    for text in mine[-ECHO:]:
        parts = [s for s in _SENTENCE.split(text) if s.strip()]
        heads.append(u'«%s»' % _head(parts[0]))
        if len(parts) > 1:
            tails.append(u'«%s»' % _tail(parts[-1]))
    lines = [u'ТАК ТЫ УЖЕ ПИСАЛ ЭТОМУ ЧЕЛОВЕКУ — не повторяйся: начни и закончи по-другому, '
             u'а объяснения, которые уже давал, скажи другими словами.',
             u'Начала: ' + u' · '.join(heads)]
    if tails:
        lines.append(u'Концовки: ' + u' · '.join(tails))
    said = u'\n'.join(mine)
    used = [u'%s — %d' % (name, len(rx.findall(said))) for name, rx in _TERMS if rx.search(said)]
    if used:
        lines.append(u'Слова метода, которые уже звучали (сколько раз): %s. Скажи это проще и '
                     u'другими словами или обойдись без них.' % u', '.join(used))
    return u'\n'.join(lines)


# ------------------------------------------------------------------ «Энергоспортзал»

# Sharp 11.11.2026: «он должен говорить не спортзал, а энергоспортзал… спортзал — это где
# качки, а энергоспортзал — именно от Павла». ИИ так и сказано (texts.EVGENY_PROMPT); это —
# страховка: «Энергетический спортзал», «энерго спортзал» и просто «спортзал» в словах
# Евгения становятся «Энергоспортзалом», окончание падежа остаётся («в Энергоспортзале»).
_GYM_FULL = re.compile(u'(?:энергетическ\\w*|энерго)[\\s\\-]+спортзал', re.I)
_GYM_BARE = re.compile(u'(?<![\\w\\-])спортзал', re.I)


def energo(text: str | None) -> str:
    return _GYM_BARE.sub(u'Энергоспортзал', _GYM_FULL.sub(u'Энергоспортзал', text or ''))


# ------------------------------------------------------------------ человек закрывает разговор

# Павел 11.11.2026: написал «хорошо, посмотрю» — а Евгений понёс лишнее и спросил снова.
# Сильные слова прощаются всегда; «хорошо», «ок», «ладно» — только если Евгений ничего не
# спрашивал: на «Рассказать, как там всё устроено?» «хорошо» значит «да, расскажите».
_CLOSING_STRONG = (
    u'спасибо|спс|благодарю|благодарствую|пасиб\\w*|понятно|ясно|понял[аи]?|принято|'
    u'посмотрю|гляну|досмотрю|пересмотрю|посмотрим|буду\\s+смотреть|подумаю|'
    u'(?:нет|нету)\\s+вопросов|вопросов\\s+(?:пока\\s+)?(?:нет|нету)|без\\s+вопросов|'
    u'и\\s+вам|вам\\s+тоже|взаимно|до\\s+свидания|всего\\s+доброго|хорошего\\s+(?:дня|вечера)|'
    u'спокойной\\s+ночи')
_CLOSING_WEAK = u'хорошо|окей|ок|ok|okay|ладно|договорились|отлично|супер|класс|здорово|прекрасно'
_STRONG = re.compile(u'\\b(?:%s)\\b' % _CLOSING_STRONG, re.I)
_ANY = re.compile(u'\\b(?:%s|%s)\\b' % (_CLOSING_STRONG, _CLOSING_WEAK), re.I)
# слова, которые прощание не меняют: «спасибо большое за марафон», «посмотрю четвёртый день ещё раз»
_FILLER = re.compile(
    u'ну|пока|тогда|я|вас|вам|тоже|и|а|уже|обязательно|конечно|всё|все|так|буду|сейчас|позже|'
    u'попозже|потом|сегодня|завтра|вечером|ещё|еще|раз|мой|друг|евгений|большое|огромное|за|'
    u'марафон|помощь|поддержку|ответ|ответы|информацию|запись|записи|день|дни|видео|'
    u'(?:перв|втор|трет|четв[её]рт)\\w*', re.I)


def closing(text: str | None, asked: bool = False) -> bool:
    u"""Человек закрывает разговор: «хорошо, посмотрю», «спасибо», «пока нет вопросов», 🙏.
    asked — в последнем сообщении бот о чём-то спросил: тогда «хорошо» и 👍 — это ответ."""
    text = (text or '').strip()
    if not text or len(text) > CLOSING_LEN or '?' in text:
        return False
    if not re.search(r'\w', text):
        return not asked                # одни эмодзи и скобки: 🙏, 👍, )))
    if not (_STRONG if asked else _ANY).search(text):
        return False
    rest = _ANY.sub(u' ', text)
    return all(_FILLER.fullmatch(word) for word in re.findall(r'\w+', rest))


class _End(NamedTuple):
    said: str           # что человек написал после последнего сообщения бота
    asked: bool         # в том сообщении бот о чём-то спросил
    at: float           # когда оно ушло
    during: bool        # человек написал всё это, пока уходили эфиры Павла


def _ending(user_id: int) -> _End:
    u"""Что человек написал после последнего сообщения бота, спросил ли бот в нём о чём-то и
    когда оно ушло. Эфиры — продолжение прощания (_settled): написанное между ними — после них."""
    history = _settled(db.chat_history(user_id, HISTORY))
    tail = []
    while history and history[-1]['side'] == 'in':
        tail.append(history.pop())
    tail.reverse()
    said = u'\n'.join(t for t in ((m.get('text') or '').strip() for m in tail) if t)
    if not history:
        return _End(said, False, 0.0, False)
    last = history[-1]
    at = float(last.get('at') or 0)
    during = bool(tail) and _attachment(last) and all(float(m.get('at') or 0) < at for m in tail)
    return _End(said, '?' in (last.get('text') or ''), at, during)


def _closed_after(user_id: int, last_out: float) -> bool:
    u"""Последнее, что написал бот, — уже ответ на прощание: второе «спасибо» подряд без ответа,
    иначе вежливости нет конца."""
    stored = db.get_content(CLOSED_KEY % user_id)
    return bool(last_out and stored and float(stored[1] or 0) >= last_out)


def _closing(text: str, said: str, ty: bool = False) -> str:
    u"""Ответ на прощание: короткие фразы без вопроса и без Энергоспортзала. Ничего такого ИИ не
    написал — заготовка по словам человека (texts.EVGENY_CLOSING_FALLBACK)."""
    kept = []
    for sentence in (s.strip() for s in _SENTENCE.split(text or '')):
        if not sentence or '?' in sentence or u'спортзал' in sentence.lower():
            continue
        if len(u' '.join(kept + [sentence])) > CLOSING_MAX:
            break
        kept.append(sentence)
    if kept:
        return u' '.join(kept)
    for words, vy, on_ty_text in texts.EVGENY_CLOSING_FALLBACK:
        if re.search(words, said or '', re.I):
            return on_ty_text if ty else vy
    return texts.EVGENY_CLOSING_FALLBACK[-1][2 if ty else 1]


# ------------------------------------------------------------------ эфиры Павла после прощания

_EFIRY = frozenset(text.strip() for text in texts.EVGENY_EFIRY)

# Человек отказался или откладывает — страховка, если ИИ, прощаясь, не поставил [BYE]
_DECLINE = re.compile(
    u'(?:нет|нету|не\\s+имею|не\\s+хватает)\\s+(?:\\w+\\s+){0,2}(?:денег|средств|возможност|финанс)|'
    u'(?:денег|средств|возможност\\w*|финанс\\w*)\\s+(?:\\w+\\s+){0,3}(?:нет|нету|не\\s+хватает)\\b|'
    u'(?:нет|нету)\\s+вопросов|вопросов\\s+(?:пока\\s+)?(?:нет|нету)|^\\s*(?:нет|неа|нету)\\W*$|'
    u'\\bнет[,.!\\s]+спасибо|подума|не\\s+сейчас|не\\s+готов|попозже|позже|в\\s+другой\\s+раз|'
    u'в\\s+следующ\\w+\\s+(?:раз|месяц)|\\bдорого\\b|дороговато|не\\s+потяну|не\\s+по\\s+карману|'
    u'с\\s+зарплаты|откажусь|воздержусь', re.I)


def declined(text: str | None) -> bool:
    u"""Человек отказался от Энергоспортзала или отложил: «денег нет», «подумаю», «не сейчас»."""
    return bool(_DECLINE.search(text or ''))


def _attachment(m: dict) -> bool:
    u"""Сообщение — эфир Павла, ушедший следом за прощанием, а не ответ Евгения."""
    return (m.get('side') == 'out' and m.get('author') == EVGENY
            and (m.get('text') or '').strip() in _EFIRY)


def _settled(rows: list[dict]) -> list[dict]:
    u"""Переписка, где эфиры стоят сразу за прощанием, к которому они относятся. Между ними
    полминуты, человек успевает написать («спасибо», вопрос) — для ИИ и для «на что отвечать»
    это написано после эфиров: ответ ему и уйдёт после них."""
    out, held = [], []
    for m in rows:
        if m['side'] == 'in':
            held.append(m)
        elif _attachment(m):
            out.append(m)
        else:
            out.extend(held)
            held = []
            out.append(m)
    return out + held


def _records_open(user_id: int, now: float | None = None) -> bool:
    u"""Записи марафона ещё открыты — 72 часа после 4-го дня: эфиры Павла начинаются словами
    «в эти 72 часа». Своему в проверке — всегда: дней у него нет, он идёт кнопками."""
    if config.is_team(user_id):
        return True
    got = db.days_of(user_id).get(4)
    return bool(got) and (now or time.time()) - got < DAY4_WAIT


def _offered(user_id: int) -> bool:
    u"""Координатор уже предложил Энергоспортзал — ещё до этого сообщения человека: он предлагает
    в ответ на рассказ об ощущениях 4-го дня, и тот уже записан. Так прощание на 3-м дне эфиры не
    зовёт, а рассказ о 4-м дне — тоже: 12.11.2026 Павел в проверке написал о нём «понял, что на дне,
    финансов нету», бот принял это за отказ, и эфиры ушли следом за самим предложением."""
    return 4 in db.feedback_days(user_id)


def _efir_due(user_id: int) -> bool:
    u"""Прощаясь сейчас, прислать ли следом эфиры: предложение уже было (в дожиме — с первых
    сообщений), записи ещё открыты, эфиров этому человеку ещё не было."""
    row = db.evgeny_get(user_id) or {}
    if row.get('efir_at') or not _records_open(user_id):
        return False
    return (row.get('mode') or 'sales') != 'coach' or _offered(user_id)


def _farewell(tags: dict, ends: bool, said: str, text: str) -> bool:
    u"""Евгений сейчас попрощался: ответил на «хорошо, посмотрю», «подумаю» (closing), сам
    поставил [BYE] или человек отказался («денег нет»), а Евгений ни о чём не спросил. Ответ на
    рассказ об ощущениях ([FEEDBACK]) отказом не считаем: «финансов нету» там — про ощущения."""
    if {'team', 'pay', 'stop', 'day', 'buy'} & set(tags):
        return False
    if ends or 'bye' in tags:
        return True
    return ('feedback' not in tags and declined(said) and '?' not in said and '?' not in text)


async def _efir(bot, user_id: int) -> bool:
    u"""Два эфира Павла следом за прощанием — подряд, через полминуты каждый (EFIR_GAP), его
    текстом слово в слово. Евгения за это время выключили (ответила команда) — не шлём."""
    for n, text in enumerate(texts.EVGENY_EFIRY):
        await asyncio.sleep(max(0.0, EFIR_GAP - _typing_seconds(text)))
        if not listening(user_id) or not await _say(bot, user_id, text):
            return False
        if n == 0:
            db.evgeny_efir(user_id)
    # проверяющему пометки нет (Павел 12.11.2026: «хрень, техническое какое-то сообщение»): вопрос
    # об эфирах и в проверке — как участнику, об этом пояснение проверки (texts.EVGENY_TRY_NOTE)
    log.info(u'Евгений: %s — эфиры Павла после прощания', user_id)
    return True


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
    for m in _settled(db.chat_history(user_id, HISTORY)):
        text = (m.get('text') or '').strip()
        if m['kind'] != 'text':
            what = u'[%s]' % _KIND_NAMES.get(m['kind'], u'вложение')
            text = (what + u' ' + text).strip()
        if not text:
            continue
        out.append({'role': 'user' if m['side'] == 'in' else 'assistant', 'content': text})
    return out


_REPLY_TASKS = (texts.EVGENY_COACH_REPLY_TASK, texts.EVGENY_REPLY_TASK)


def _request(user_id: int, task: str, mode: str | None = None, closing_said: str = '',
             efir: bool = False) -> list[dict]:
    u"""closing_said — человек закрывает разговор этими словами: вместо жребия формы — одна
    короткая фраза (texts.EVGENY_CLOSING_TASK); efir — следом уйдут эфиры Павла. Эфиры уже
    ушли — Евгений снова подводит к Энергоспортзалу (texts.EVGENY_EFIR_TALK)."""
    role = texts.EVGENY_COACH_TASK if (mode or _mode(user_id)) == 'coach' else texts.EVGENY_SALES_TASK
    # Первое системное — одно на всех людей в этом режиме: Claude держит его в кэше
    # (bot/ai.py). Что известно о человеке — отдельным, следом. В историях команды —
    # «пошёл в спортзал»: ИИ видит их уже с «Энергоспортзалом», чтобы не перенял слово.
    rules = (texts.EVGENY_PROMPT + u'\n\n' + role + u'\n\n' + texts.EVGENY_SENSES
             + u'\n\nФАКТЫ\n' + facts()
             + u'\n\nИСТОРИИ УЧАСТНИКОВ\n' + (energo(cases()) or texts.EVGENY_CASES_NONE))
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
    last = [task]
    if closing_said:
        last.append(texts.EVGENY_CLOSING_TASK.format(said=_head(closing_said, 120)))
        if efir:
            last.append(texts.EVGENY_CLOSING_EFIR)
    elif task in _REPLY_TASKS:
        last.append(_style())           # ответ человеку — у остальных задач форма своя
        if (db.evgeny_get(user_id) or {}).get('efir_at'):
            last.append(texts.EVGENY_EFIR_TALK)
    echo = _echo(user_id)
    if echo:
        last.append(echo)
    messages.append({'role': 'system', 'content': u'\n\n'.join(last)})
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
    text = energo(text)
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
        reply = task in _REPLY_TASKS
        end = _ending(user_id) if reply else _End('', False, 0.0, False)
        said = end.said
        ends = closing(said, end.asked)
        if ends and (end.during or _closed_after(user_id, end.at)):
            # «спасибо», пока уходили эфиры, — ответом и были эфиры; второе «спасибо» подряд — тоже
            log.info(u'Евгений: %s снова прощается — на прощание уже ответили, молчим', user_id)
            return False
        due = reply and _efir_due(user_id)
        try:
            raw = await ai.complete(_request(user_id, task, closing_said=said if ends else '',
                                             efir=due and ends))
        except ai.AIError as err:
            log.warning(u'Евгений: ИИ не ответил для %s: %s', user_id, err)
            await _team(bot, user_id, u'🤖 <b>Евгений не смог ответить</b>\n%s\n%s\n%s' % (
                _who(user_id), html.escape(str(err)), texts.REPLY_HINT))
            return False
        if not listening(user_id):
            # пока ИИ думал, человеку ответила команда — второй ответ следом не нужен
            return False
        text, tags = parse(raw)
        if 'ty' in tags:
            _set_ty(user_id)
        coach_mode = _mode(user_id) == 'coach'
        # координатор кнопку покупки не даёт: об оплате за него говорит команда
        buy = 'buy' in tags and not coach_mode
        if coach_mode and 'pay' in tags:
            text = without_money(text)
        if ends and not ({'team', 'pay', 'stop', 'day'} & set(tags)):
            text = _closing(text, said, on_ty(user_id))
        if not text:
            text = texts.EVGENY_FALLBACK if 'team' in tags and 'pay' not in tags else ''
        sent = bool(text) and await _say(bot, user_id, text, buy=buy)
        if sent and ends:
            db.put_content(CLOSED_KEY % user_id, 'ts', str(time.time()))
        for day in tags.get('feedback', ()):
            try:
                db.log_event(user_id, 'feedback', str(day))
            except Exception as err:            # отметка — не повод молчать
                log.warning(u'Евгений: не отметили обратную связь %s у %s: %s', day, user_id, err)
        if sent and reply and coach_mode:
            # спросил в разговоре о дне — промолчит человек SENDOFF_AFTER, Евгений пожелает
            # смотреть следующий день (_sendoffs). Ответил без вопроса — ждать нечего; прислал
            # запись дня ещё раз — человек её и смотрит; об оплате — отвечает команда.
            asks = '?' in text and not ({'day', 'pay'} & set(tags))
            db.evgeny_asked(user_id, _talk_day(user_id) if asks else None)
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
        elif sent and due and _farewell(tags, ends, said, text):
            await _efir(bot, user_id)       # под тем же замком: ответ на «спасибо» — после эфиров
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


_WANT = re.compile(u'хоч|вступ|купить|куплю|соглас|готов', re.I)


def _wants_gym(ask: str | None) -> bool:
    u"""Суть от ИИ — «хочет в спортзал»: человек согласился, а не спрашивает цену."""
    ask = (ask or '').lower()
    return u'спортзал' in ask and bool(_WANT.search(ask))


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
    # согласие на спортзал после предложения 4-го дня — команде сразу видно, что это покупка;
    # «сколько стоит спортзал» — ещё вопрос об оплате, а не согласие
    title = (texts.EVGENY_PAY_TITLE_MASTER if master
             else texts.EVGENY_PAY_TITLE_GYM if _wants_gym(ask)
             else texts.EVGENY_PAY_TITLE)
    await _team(bot, user_id, texts.EVGENY_TEAM_PAY.format(
        title=title,
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
    ty = on_ty(user_id)
    if before:
        again = texts.EVGENY_COACH_AGAIN_TY if ty else texts.EVGENY_COACH_AGAIN
        text = again.format(after=texts.EVGENY_COACH_AFTER[max(before)],
                            sphere=texts.EVGENY_COACH_SPHERE[day])
    else:
        ask = texts.EVGENY_COACH_ASK_TY if ty else texts.EVGENY_COACH_ASK
        text = ask.format(day=texts.EVGENY_COACH_DAY[day])
    return text if wrote else texts.EVGENY_COACH_HELLO + text


async def _worded(user_id: int, task: str, what: str) -> str:
    u"""Сообщение по сценарию словами ИИ — каждый раз своими (Sharp 11.11.2026: «намного
    разнообразнее»). Пусто — уходит текст заказчика: ИИ молчит, думает дольше ASK_TIMEOUT или
    пишет не то. Шаг воронки ждёт не дольше ASK_TIMEOUT."""
    if not ASK_BY_AI or not ai.ready():
        return ''
    try:
        raw = await asyncio.wait_for(ai.complete(_request(user_id, task, mode='coach')), ASK_TIMEOUT)
    except Exception as err:            # сообщение уйдёт в любом случае — словами заказчика
        log.warning(u'Евгений: %s для %s — текстом заказчика (%s)', what, user_id,
                    err or err.__class__.__name__)
        return ''
    text, tags = parse(raw)
    if not text or tags or len(text) > ASK_MAX:
        log.warning(u'Евгений: ИИ написал не то (%s) для %s — текст заказчика', what, user_id)
        return ''
    return text


async def _ask_text(user_id: int, day: int, fallback: str) -> str:
    u"""Вопрос про ощущения N-го дня словами ИИ (_worded); смысл — текст заказчика, он же и
    запасной."""
    told = [d for d in db.feedback_days(user_id) if d < day]
    task = texts.EVGENY_ASK_TASK.format(
        day=day, name=texts.EVGENY_DAY_NAMES[day],
        before=texts.EVGENY_ASK_BEFORE.format(day=max(told)) if told else u'')
    text = await _worded(user_id, task, u'вопрос про %d-й день' % day)
    if not text:
        return fallback
    return text if db.wrote_as(user_id, EVGENY) else texts.EVGENY_COACH_HELLO + text


async def coach_message(user_id: int, ref: str) -> str:
    u"""Что написать по сценарию: знакомство — текст заказчика как есть, вопрос про день —
    словами ИИ (_ask_text). Пусто — писать нечего (coach_text)."""
    text = coach_text(user_id, ref)
    if not text or ref == 'intro':
        return text
    return await _ask_text(user_id, int(ref[3:]), text)


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
    text = await coach_message(user_id, ref)
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
    u"""Первое сообщение проверки — то же знакомство, что получает участник. Сразу, без
    «печатает…»: 11.11.2026 Павел нажал «1-й день», пока знакомство «печаталось» 8 секунд, —
    вопрос про день пришёл первым, а знакомство следом, второй раз."""
    return await _say(bot, user_id, coach_text(user_id, 'intro') or texts.EVGENY_COACH_INTRO,
                      typing=False)


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
    text = await coach_message(user_id, 'day%d' % day)
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
    if offers_ty(message.text or message.caption):
        _set_ty(user_id)                # до ответа ИИ: в карточке уже будет «на ты»
    if getattr(message, 'voice', None) or getattr(message, 'video_note', None):
        # на серию голосовых — одна просьба, а не по одной на каждое
        if time.time() - _voice_told.get(user_id, 0) > VOICE_GAP:
            _voice_told[user_id] = time.time()
            voice = texts.EVGENY_VOICE_TY if on_ty(user_id) else texts.EVGENY_VOICE
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
        if db.wrote_as(user_id, EVGENY):
            # вёл его марафон координатором — продолжает тот разговор, а не здоровается
            sent = await _turn(bot, user_id, texts.EVGENY_PUSH_TASK)
        else:
            sent = await _say(bot, user_id, opener(), mass=True)
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


async def _efir_asks(bot, now: float) -> int:
    u"""EFIR_QUIET тишины после эфиров — Евгений один раз спрашивает, смотрели ли их, словами Павла.
    Только днём и своему в проверке — так же, как участнику: в проверке через 2 минуты Павел
    12.11.2026 назвал вопрос лишним — «он только скинул два длинных эфира, так слишком быстро
    спрашивать». Евгения остановили — спросит, когда запустят, но не позже EFIR_LATEST после эфиров."""
    done = 0
    for row in db.evgeny_efir_waiting():
        user_id = row['user_id']
        quiet = max(row.get('last_in') or 0, row.get('last_out') or 0, row['efir_at'])
        if now - quiet < EFIR_QUIET:
            continue
        gone = not config.is_team(user_id) and why_not(user_id)
        if gone or now - row['efir_at'] > EFIR_LATEST:
            db.evgeny_efir_asked(user_id)   # спрашивать некого или уже поздно
            continue
        lock = _locks.setdefault(user_id, asyncio.Lock())
        if not daytime(now) or not listening(user_id) or lock.locked():
            continue                        # ночь, Евгений остановлен или как раз отвечает
        async with lock:
            text = texts.EVGENY_EFIR_ASK_TY if on_ty(user_id) else texts.EVGENY_EFIR_ASK
            sent = await _say(bot, user_id, text, typing=False)
            db.evgeny_efir_asked(user_id)   # не ушло — не долбим каждые полминуты
        if sent:
            done += 1
            log.info(u'Евгений: %s — спросил, как эфиры', user_id)
    return done


async def efir_tick(bot, now: float | None = None) -> int:
    u"""Вопрос об эфирах — и своим в проверке, когда участникам Евгений ещё не запущен."""
    if not ai.ready():
        return 0
    return await _efir_asks(bot, now or time.time())


# ------------------------------------------------------------------ «смотрите следующий день»

def _talk_day(user_id: int) -> int | None:
    u"""О каком дне сейчас разговор: о том, про который человек рассказывал последним, — если
    рассказал недавно (SENDOFF_FRESH) и после этого дня марафона есть следующий."""
    last = db.last_feedback(user_id)
    if not last or last[0] >= 4 or time.time() - last[1] > SENDOFF_FRESH:
        return None
    return last[0]


def _unanswered(user_id: int, asked_at: float) -> bool:
    u"""После вопроса в переписке пусто: человек не писал, и бот — ни Евгений, ни рассылка, ни
    шаг воронки — тоже."""
    if ((db.evgeny_get(user_id) or {}).get('last_in') or 0) >= asked_at:
        return False
    last = db.chat_history(user_id, 1)
    return bool(last) and last[-1]['at'] <= asked_at and last[-1].get('author') == EVGENY


async def _sendoff_text(user_id: int, day: int) -> str:
    u"""Пожелание смотреть день после day и прислушиваться к телу — словами ИИ, а не вышло —
    запасным текстом по словам Павла."""
    nxt = day + 1
    later = '' if nxt in _progress(user_id)[0] else texts.EVGENY_SENDOFF_LATER
    task = texts.EVGENY_SENDOFF_TASK.format(minutes=SENDOFF_AFTER // 60, day=nxt,
                                            name=texts.EVGENY_DAY_NAMES[nxt], later=later)
    fallback = texts.EVGENY_SENDOFF_TY if on_ty(user_id) else texts.EVGENY_SENDOFF
    return (await _worded(user_id, task, u'пожелание смотреть %d-й день' % nxt)
            or fallback.format(day=texts.EVGENY_COACH_DAY[nxt]))


async def _sendoffs(bot, now: float) -> int:
    u"""Евгений спросил в разговоре о дне, а человек SENDOFF_AFTER молчит (Павел 12.11.2026: «надо,
    если человек не ответил 10 минут, то он пишет пожелание смотреть второй день и ощущения по
    телу»). Один раз после каждого дня и только пока Евгений слушает человека — участнику в
    любое время суток: тот писал минуты назад. Ответит человек позже — разговор пойдёт как обычно."""
    done = 0
    for row in db.evgeny_unanswered(now - SENDOFF_AFTER, now - SENDOFF_FRESH):
        user_id, day = row['user_id'], row['ask_day']
        lock = _locks.setdefault(user_id, asyncio.Lock())
        if not listening(user_id) or lock.locked():
            continue                        # Евгений остановлен или как раз отвечает
        async with lock:
            if not _unanswered(user_id, row['ask_at']):
                db.evgeny_asked(user_id, None)      # после вопроса уже что-то было — ждать нечего
                continue
            text = await _sendoff_text(user_id, day)
            if not listening(user_id) or not _unanswered(user_id, row['ask_at']):
                continue                    # пока ИИ писал, человек ответил или Евгения остановили
            sent = await _say(bot, user_id, text, typing=False)
            db.evgeny_sent_off(user_id, day)        # не ушло — не долбим каждые полминуты
        if sent:
            done += 1
            log.info(u'Евгений: %s молчит на вопрос о %d-м дне — пожелал смотреть следующий',
                     user_id, day)
    return done


async def sendoff_tick(bot, now: float | None = None) -> int:
    u"""«Смотрите следующий день» — и своим в проверке, когда участникам Евгений ещё не запущен."""
    if not ai.ready():
        return 0
    return await _sendoffs(bot, now or time.time())


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
    u"""Циклы рядом: оплата не ждёт, пока уйдут первые сообщения дожима (ревью 06.11.2026:
    обещанные 5 минут иначе растягивались на 6–8); вопрос об эфирах и «смотрите следующий
    день» — и своим в проверке."""
    log.info(u'Евгений: циклы дожима, оплаты, эфиров и «следующего дня» запущены')
    await asyncio.gather(_every(u'дожим', tick, bot), _every(u'оплата', pay_tick, bot),
                         _every(u'эфиры', efir_tick, bot),
                         _every(u'следующий день', sendoff_tick, bot))
