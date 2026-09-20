# -*- coding: utf-8 -*-
u"""Пульт админа: мини-приложение Telegram рядом с ботом.

AleX 17.09.2026: «нельзя какой-нибудь чат чтобы открывался с тем или иным
клиентом который в марафон сейчас что-то присылает — тыкал на него, и у
меня с ним как будто бы диалог». Реплаями это делается неудобно:
уведомления тонут в ленте, а людям со старых уведомлений писать нечем.

Пульт открывается кнопкой в боте, живёт в том же процессе и работает с той
же базой. Отдельного сервера и пароля нет: Telegram сам подписывает, кто
открыл пульт, — подпись проверяется токеном бота (check_data), и дальше
пускаем только команду проекта (config.is_team).

Без WEBAPP_URL и PORT сервер не поднимается: на машине разработчика пульт
не нужен, а мини-приложение всё равно работает только по https.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

from aiohttp import web

from . import blacklist, broadcast, config, db, delivery, record

log = logging.getLogger(__name__)

STATIC = os.path.join(config.ROOT, 'webapp')
# Сколько живёт подпись Telegram. Сутки: пульт держат открытым весь день,
# а протухшую подпись мини-приложение обновляет само при следующем входе.
MAX_AGE = 24 * 3600
PAGE_SIZE = 60
# Предел сообщения у Telegram — 4096 знаков; режем чуть раньше, чтобы
# человеку в пульте пришёл понятный отказ, а не ошибка от Telegram.
MAX_TEXT = 4000
# Скольким можно написать разом из списка галочками. Дальше — не выбор, а
# рассылка: у неё свои кнопки, доклад о ходе и продолжение после
# перезапуска (/рассылка, bot/broadcast.py). Двести — это десять секунд
# отправки, столько запрос подождёт спокойно.
MAX_PICKED = 200
# Сколько чатов Павла опрашивать для карточки: каждый — отдельный
# запрос к Telegram, а карточка должна открываться сразу.
CHATS_IN_CARD = 8
# Предел записи из пульта. Минута голосового весит килобайты, минута
# кружка — единицы мегабайт; двадцать даём с запасом на длинные записи и
# щедрые кодеки телефонов.
MAX_UPLOAD = 20 * 1024 * 1024


class Denied(Exception):
    u"""Пульт открыт не тем, кому можно."""


def check_data(init_data: str, token: str, now: float | None = None) -> dict:
    u"""Разобрать подпись Telegram и вернуть, кто открыл пульт.

    Telegram подписывает данные ключом, выведенным из токена бота: подделать
    их, не зная токена, нельзя. Проверяем подпись и возраст — иначе
    перехваченная ссылка работала бы вечно.
    """
    if not token:
        raise Denied(u'бот без токена')
    pairs = dict(urllib.parse.parse_qsl(init_data or '', keep_blank_values=True))
    got = pairs.pop('hash', '')
    if not got:
        raise Denied(u'нет подписи')
    check = u'\n'.join(u'%s=%s' % (k, pairs[k]) for k in sorted(pairs))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        raise Denied(u'подпись не сошлась')
    issued = float(pairs.get('auth_date') or 0)
    if (time.time() if now is None else now) - issued > MAX_AGE:
        raise Denied(u'подпись устарела')
    try:
        return json.loads(pairs.get('user') or '{}')
    except ValueError:
        raise Denied(u'не разобрали, кто открыл')


def _who(request) -> dict:
    u"""Кто дёргает пульт; исключение — если не команда проекта."""
    init = request.get('init_data') or ''
    user = check_data(init, config.BOT_TOKEN)
    if not user.get('id') or not config.is_team(int(user['id'])):
        raise Denied(u'этот пульт — для команды проекта')
    return user


async def _guarded(handler, request):
    u"""Общая обёртка: подпись, разбор тела, понятная ошибка вместо 500.

    Тело разбираем осторожно: пульт висит на публичном адресе, и первым,
    что его увидит, будет не команда, а поисковый робот или сканер. Любое
    мусорное тело — «null», список, число — должно упираться в вежливый
    отказ, а не в исключение: бот и пульт живут в одном процессе.
    """
    request['body'] = await _body(request)
    init = request['body'].get('initData', '')
    request['init_data'] = init if isinstance(init, str) else ''
    try:
        user = _who(request)
    except Denied as why:
        return web.json_response({'error': str(why)}, status=403)
    try:
        return await handler(request, user)
    except Exception as err:               # пульт не должен падать молча
        log.exception(u'пульт: сбой в %s', request.path)
        return web.json_response({'error': str(err)[:300]}, status=500)


def route(handler):
    async def wrapper(request):
        return await _guarded(handler, request)
    return wrapper


async def _body(request) -> dict:
    u"""Тело запроса словарём. Что угодно другое — пустой словарь."""
    if 'body' in request:
        return request['body']
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# --------------------------------------------------------------- данные

def _person(row: dict) -> dict:
    u"""Человек для списка: только то, что показывает пульт."""
    return {
        'id': row['user_id'],
        'name': row.get('first_name') or u'Без имени',
        'username': row.get('username') or '',
        'source': row.get('source') or '',
        'launched': bool(row.get('launched_at')),
        'blocked': bool(row.get('blocked_at')),
        'banned': bool(row.get('banned')),
        'poll': row.get('poll') or '',
        'waiting': int(row.get('waiting') or 0),
        'last_at': row.get('last_at'),
        'last_side': row.get('last_side') or '',
        'last_text': (row.get('last_text') or '')[:120],
        'last_kind': row.get('last_kind') or '',
    }


@route
async def api_people(request, user):
    u"""Люди для списка.

    Переписка копится только с 18.09.2026 — до этого сообщения нигде не
    сохранялись. Поэтому если на вкладке «Переписки» пусто, показываем
    всех, кто заходил в бота: пустой экран выглядел бы поломкой, а писать
    людям можно и тем, кто нам ещё ни разу не писал.
    """
    body = await _body(request)
    query = str(body.get('query') or '')[:64]
    only_chats = bool(body.get('onlyChats', True))
    rows = db.people(query, PAGE_SIZE, only_chats=only_chats)
    everyone = False
    if only_chats and not rows and not query.strip():
        rows = db.people('', PAGE_SIZE, only_chats=False)
        everyone = bool(rows)
    return web.json_response({'people': [_person(r) for r in rows], 'everyone': everyone})


# Как назвать шаг воронки в ленте диалога.
DAY_NOTE = u'Бот прислал запись %s дня'
POLL_NOTE = u'Бот спросил: посмотрел %s день?'
OFFER_NOTE = u'Бот прислал кнопки покупки'
ANSWER_NOTE = u'Ответ на вопрос %s дня: %s'
ORDINAL = {'1': u'первого', '2': u'второго', '3': u'третьего', '4': u'четвёртого'}
ORDINAL_ASK = {'day1': u'первый', 'day2': u'второй', 'day3': u'третий'}
YES_NO = {'yes': u'Да', 'no': u'Нет'}


def _note(step: dict) -> str:
    u"""Шаг воронки строкой для человека, а не для программиста."""
    ref = str(step.get('ref') or '')
    if step['kind'] == 'day':
        return DAY_NOTE % ORDINAL.get(ref, ref)
    if step['kind'] == 'poll':
        return POLL_NOTE % ORDINAL_ASK.get(ref, ref)
    if step['kind'] == 'answer':
        # «Ответ на вопрос первого дня» — падеж тот же, что у записи дня.
        return ANSWER_NOTE % (ORDINAL.get(ref.replace('day', ''), ref),
                              YES_NO.get(step.get('answer'), step.get('answer') or ''))
    return OFFER_NOTE


def _messages(user_id: int) -> list[dict]:
    u"""Лента диалога: сообщения вперемешку с шагами воронки, по времени.

    Внутренние id отправителя и вложения наружу не отдаём — пульту они не
    нужны, а любое лишнее поле в ответе рано или поздно где-нибудь всплывёт.
    """
    lines = [{'id': m['id'], 'kind': m['kind'], 'text': m['text'], 'at': m['at'],
              'mine': m['side'] == 'out',
              # править и удалять можно только своё и только текстовое
              'can_edit': bool(m['side'] == 'out' and m['tg_id'] and m['kind'] == 'text'),
              'can_drop': bool(m['side'] == 'out' and m['tg_id'])}
             for m in db.chat_history(user_id)]
    lines += [{'id': 0, 'note': True, 'text': _note(step), 'at': step['at'],
               'kind': 'text', 'mine': False} for step in db.timeline(user_id)]
    return sorted(lines, key=lambda line: line['at'])


async def _chats_of(bot, user_id: int) -> list[dict]:
    u"""В каких чатах Павла человек состоит.

    AleX 18.09.2026: «подписчик ли он ФИНИШ (канала/чата №?)». Telegram
    отвечает на это только поимённо по каждому чату, поэтому спрашиваем
    лишь там, где бот сам состоит, и не больше нескольких чатов: карточка
    должна открываться быстро.
    """
    out = []
    for chat in db.known_chats()[:CHATS_IN_CARD]:
        try:
            member = await bot.get_chat_member(chat['chat_id'], user_id)
        except Exception:
            continue                       # бота выгнали или чат недоступен
        status = getattr(member, 'status', '')
        status = getattr(status, 'value', status)
        if status in ('left', 'kicked'):
            continue
        out.append({'title': chat.get('title') or str(chat['chat_id']),
                    'status': u'забанен' if status == 'kicked' else u'состоит'})
    return out


def _card(person: dict) -> dict:
    u"""Путь человека по марафону: когда начал, что получил, что ответил."""
    answers = db.answers_of(person['user_id'])
    days = db.days_of(person['user_id'])
    return {
        'started_at': person.get('started_at'),
        'launched_at': person.get('launched_at'),
        'launches': person.get('launches') or 0,
        'lead_no': person.get('lead_no'),
        'days': [{'day': day,
                  'at': days.get(day),
                  'answer': (answers.get('day%d' % day) or (None,))[0]}
                 for day in (1, 2, 3, 4)],
    }


@route
async def api_chat(request, user):
    body = await _body(request)
    person = db.get_user(int(body.get('id') or 0))
    if not person:
        return web.json_response({'error': u'человека нет в базе'}, status=404)
    return web.json_response({
        'person': _person(dict(person, waiting=0, banned=db.is_banned(person['user_id']),
                               last_at=None)),
        'card': _card(person),
        'chats': await _chats_of(request.app['bot'], person['user_id']),
        'messages': _messages(person['user_id']),
    })


# Что за вложение прислали ссылкой — по концу адреса. AleX 18.09.2026:
# «отправить сообщение с креплением ссылки на видео, на фото, на любой
# медиа или подкаст». Файл к Telegram идёт напрямую по этой ссылке, через
# пульт он не проходит: пульт живёт в одном процессе с ботом, и качать
# через него чужие гигабайты нельзя.
MEDIA_KINDS = (
    (('.jpg', '.jpeg', '.png', '.webp'), 'photo'),
    (('.mp4', '.mov', '.m4v'), 'video'),
    (('.mp3', '.m4a', '.ogg', '.oga', '.wav'), 'audio'),
    (('.gif',), 'animation'),
    (('.pdf', '.doc', '.docx', '.zip'), 'document'),
)


def media_kind(url: str) -> str:
    u"""Вид вложения по ссылке. Неизвестное — ссылкой текстом."""
    tail = urllib.parse.urlparse(url).path.lower()
    for endings, kind in MEDIA_KINDS:
        if tail.endswith(endings):
            return kind
    return ''


def check_media(url: str) -> str:
    u"""Ссылка, годная для отправки. Пустая строка — не годится."""
    url = (url or '').strip()
    if not url or len(url) > 1024:
        return ''
    parts = urllib.parse.urlparse(url)
    return url if parts.scheme in ('http', 'https') and '.' in parts.netloc else ''


async def send_media(bot, user_id: int, url: str, caption: str) -> tuple:
    u"""Отправить вложение по ссылке. Возвращает вид отправленного."""
    kind = media_kind(url)
    senders = {
        'photo': bot.send_photo,
        'video': bot.send_video,
        'audio': bot.send_audio,
        'animation': bot.send_animation,
        'document': bot.send_document,
    }
    if kind not in senders:
        # Не узнали вложение — пусть уходит ссылкой: Telegram покажет превью.
        body = (caption + u'\n' + url) if caption else url
        sent = await delivery._guard(bot.send_message(user_id, body, parse_mode=None))
        return 'text', sent
    sent = await delivery._guard(senders[kind](user_id, url, caption=caption or None))
    return kind, sent


def picked_ids(source) -> list:
    u"""Кого отметили галочками в списке. Берём только тех, кто есть в базе.

    Из формы записи (multipart) приходит строкой через запятую, из обычного
    запроса — списком; принимаем оба вида.
    """
    raw = source.get('ids')
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_PICKED]:
        try:
            user_id = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if user_id and user_id not in out and db.get_user(user_id):
            out.append(user_id)
    return out


async def to_each(ids: list, one) -> web.Response:
    u"""Одно и то же — каждому из выбранных.

    По одному и с той же паузой, что у общей рассылки: Telegram принимает
    от бота около тридцати сообщений в секунду на всех, и «написать
    выбранным» не должно мешать самой воронке идти.

    Закрывшего бота отмечаем и идём дальше: из-за одного ушедшего
    остальные девятнадцать сообщение получить обязаны.
    """
    sent = gone = failed = 0
    for user_id in ids:
        try:
            await one(user_id)
        except delivery.Gone:
            db.mark_blocked(user_id)
            gone += 1
        except Exception as err:
            log.warning(u'пульт: выбранным — %s не ушло: %s', user_id, err)
            failed += 1
        else:
            sent += 1
        await asyncio.sleep(broadcast.PAUSE)
    return web.json_response({'sent': sent, 'gone': gone, 'failed': failed})


@route
async def api_send(request, user):
    u"""Отправить человеку сообщение от имени бота."""
    body = await _body(request)
    user_id = int(body.get('id') or 0)
    text = str(body.get('text') or '').strip()
    media = check_media(str(body.get('media') or ''))
    if not text and not media:
        return web.json_response({'error': u'пустое сообщение'}, status=400)
    if str(body.get('media') or '').strip() and not media:
        return web.json_response(
            {'error': u'ссылка на вложение должна начинаться с http:// или https://'},
            status=400)
    if len(text) > MAX_TEXT:
        return web.json_response(
            {'error': u'слишком длинное: %d знаков, влезает %d' % (len(text), MAX_TEXT)},
            status=400)

    bot = request.app['bot']
    chosen = picked_ids(body)
    if chosen:
        async def one(uid):
            if media:
                kind, sent = await send_media(bot, uid, media, text)
            else:
                kind = 'text'
                sent = await delivery._guard(bot.send_message(uid, text, parse_mode=None))
            db.save_message(uid, 'out', kind, text or media, media or None,
                            author=int(user['id']), tg_id=getattr(sent, 'message_id', None))

        log.info(u'пульт: %s пишет %d выбранным', user['id'], len(chosen))
        return await to_each(chosen, one)

    person = db.get_user(user_id)
    if not person:
        return web.json_response({'error': u'человека нет в базе'}, status=404)
    sent = None
    try:
        if media:
            kind, sent = await send_media(bot, user_id, media, text)
        else:
            kind = 'text'
            sent = await delivery._guard(bot.send_message(user_id, text, parse_mode=None))
    except delivery.Gone:
        db.mark_blocked(user_id)
        return web.json_response({'error': u'человек закрыл бота — писать ему нельзя'}, status=409)
    db.save_message(user_id, 'out', kind, text or media, media or None,
                    author=int(user['id']), tg_id=getattr(sent, 'message_id', None))
    log.info(u'пульт: %s написал(а) человеку %s', user['id'], user_id)
    return web.json_response({'messages': _messages(user_id)})


@route
async def api_edit(request, user):
    u"""Поправить своё отправленное сообщение — и у человека тоже."""
    body = await _body(request)
    line = db.message(int(body.get('messageId') or 0))
    text = str(body.get('text') or '').strip()
    if not line or line['side'] != 'out' or not line['tg_id']:
        return web.json_response({'error': u'это сообщение править нельзя'}, status=400)
    if not text or len(text) > MAX_TEXT:
        return web.json_response({'error': u'пустое или слишком длинное сообщение'}, status=400)
    try:
        await request.app['bot'].edit_message_text(
            chat_id=line['user_id'], message_id=line['tg_id'], text=text, parse_mode=None)
    except Exception as err:
        return web.json_response({'error': _telegram_reason(err)}, status=409)
    db.edit_message(line['id'], text)
    log.info(u'пульт: %s поправил(а) сообщение %s', user['id'], line['id'])
    return web.json_response({'messages': _messages(line['user_id'])})


@route
async def api_drop(request, user):
    u"""Удалить своё отправленное сообщение у человека."""
    body = await _body(request)
    line = db.message(int(body.get('messageId') or 0))
    if not line or line['side'] != 'out' or not line['tg_id']:
        return web.json_response({'error': u'это сообщение удалить нельзя'}, status=400)
    try:
        await request.app['bot'].delete_message(chat_id=line['user_id'],
                                                message_id=line['tg_id'])
    except Exception as err:
        return web.json_response({'error': _telegram_reason(err)}, status=409)
    db.drop_message(line['id'])
    log.info(u'пульт: %s удалил(а) сообщение %s', user['id'], line['id'])
    return web.json_response({'messages': _messages(line['user_id'])})


def _telegram_reason(err: Exception) -> str:
    u"""Почему Telegram отказал — словами, а не кодом.

    Своё сообщение бот может удалить только двое суток, а править —
    пока человек не закрыл бота; чаще всего отказ именно об этом.
    """
    text = str(err)
    if 'too old' in text or "can't be deleted" in text:
        return u'прошло больше двух суток — Telegram больше не даёт его трогать'
    if 'not modified' in text:
        return u'текст тот же самый'
    if 'not found' in text:
        return u'сообщение уже удалено'
    return text[:200]


async def api_record(request):
    u"""Голосовое или кружок, записанные в пульте (AleX 19.09.2026).

    Тело читаем частями, а не целиком: первым идёт подпись, и пока она не
    сошлась, файл мы даже не начинаем принимать. Иначе публичный адрес
    пульта позволял бы кому угодно залить в процесс бота двадцать
    мегабайт до всякой проверки.
    """
    try:
        parts = await request.multipart()
    except Exception:
        return web.json_response({'error': u'не разобрали запись'}, status=400)

    fields, blob, name = {}, b'', ''
    while True:
        part = await parts.next()
        if part is None:
            break
        if part.name == 'file':
            if not _who_safe(fields):
                return web.json_response({'error': u'этот пульт — для команды проекта'},
                                         status=403)
            name = part.filename or 'record'
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                blob += chunk
                if len(blob) > MAX_UPLOAD:
                    return web.json_response(
                        {'error': u'запись длиннее, чем бот принимает — запишите короче'},
                        status=413)
        else:
            fields[part.name] = (await part.read(decode=True)).decode('utf-8', 'replace')

    user = _who_safe(fields)
    if not user:
        return web.json_response({'error': u'этот пульт — для команды проекта'}, status=403)
    if not blob:
        return web.json_response({'error': u'пустая запись'}, status=400)

    user_id = int(fields.get('id') or 0)
    kind = 'note' if fields.get('kind') == 'note' else 'voice'
    caption = (fields.get('text') or '').strip()[:1024]

    chosen = picked_ids(fields)
    if chosen:
        bot = request.app['bot']
        # Перекодируем один раз на всех: ffmpeg на каждого из двадцати —
        # это полминуты ожидания на ровном месте.
        try:
            ready, saved = await _make_record(kind, blob, name)
        except Exception as err:
            log.exception(u'пульт: запись выбранным не собралась')
            return web.json_response({'error': str(err)[:300]}, status=500)
        try:
            log.info(u'пульт: %s шлёт %s %d выбранным', user['id'], saved, len(chosen))
            return await to_each(chosen, lambda uid: _push_record(
                bot, uid, saved, ready, name, caption, int(user['id'])))
        finally:
            record.forget(*ready[1])

    if not db.get_user(user_id):
        return web.json_response({'error': u'человека нет в базе'}, status=404)

    try:
        return await _send_record(request.app['bot'], user_id, kind, blob, name,
                                  caption, int(user['id']))
    except delivery.Gone:
        db.mark_blocked(user_id)
        return web.json_response({'error': u'человек закрыл бота — писать ему нельзя'}, status=409)
    except Exception as err:
        log.exception(u'пульт: запись не ушла')
        return web.json_response({'error': str(err)[:300]}, status=500)


def _who_safe(fields: dict):
    u"""Кто прислал запись; None — подпись не сошлась или он не из команды."""
    try:
        user = check_data(fields.get('initData') or '', config.BOT_TOKEN)
    except Denied:
        return None
    if not user.get('id') or not config.is_team(int(user['id'])):
        return None
    return user


async def _make_record(kind: str, blob: bytes, name: str):
    u"""Перекодировать запись один раз. Возвращает ((байты, имя), чем стало).

    Отдельно от отправки затем, что выбранных бывает двадцать, а ffmpeg на
    каждого — это полминуты ожидания на ровном месте. Во втором элементе —
    файлы, которые надо убрать: record.forget(*ready[1]).
    """
    source = record.keep(blob, os.path.splitext(name)[1] or '.webm')
    ready = None
    try:
        if record.have_ffmpeg():
            ready = await (record.to_note(source) if kind == 'note'
                           else record.to_voice(source))
        data = open(ready or source, 'rb').read()
    except Exception:
        record.forget(source, ready)
        raise
    if ready and kind == 'note':
        saved = 'video_note'
    elif ready:
        saved = 'voice'
    else:
        # Без перекодировки Telegram не примет ни голосовое, ни кружок —
        # отправляем обычным файлом, чтобы запись всё-таки дошла.
        saved = 'document'
    return (data, (source, ready)), saved


async def _push_record(bot, user_id: int, saved: str, ready, name: str,
                       caption: str, author: int):
    u"""Отправить готовую запись одному человеку и записать в переписку."""
    from aiogram.types import BufferedInputFile

    data = ready[0]
    if saved == 'video_note':
        sent = await delivery._guard(bot.send_video_note(
            user_id, BufferedInputFile(data, 'note.mp4')))
    elif saved == 'voice':
        sent = await delivery._guard(bot.send_voice(
            user_id, BufferedInputFile(data, 'voice.ogg'), caption=caption or None))
    else:
        sent = await delivery._guard(bot.send_document(
            user_id, BufferedInputFile(data, name or 'record.webm'),
            caption=caption or None))
    db.save_message(user_id, 'out', saved, caption or None, None, author,
                    getattr(sent, 'message_id', None))
    log.info(u'пульт: %s отправил(а) %s человеку %s', author, saved, user_id)
    return sent


async def _send_record(bot, user_id: int, kind: str, blob: bytes, name: str,
                       caption: str, author: int):
    u"""Перекодировать запись и отправить человеку."""
    ready, saved = await _make_record(kind, blob, name)
    try:
        await _push_record(bot, user_id, saved, ready, name, caption, author)
    finally:
        record.forget(*ready[1])
    return web.json_response({'messages': _messages(user_id), 'kind': saved})


@route
async def api_ban(request, user):
    u"""Чёрный список прямо из пульта: и в боте, и в каналах Павла."""
    body = await _body(request)
    user_id = int(body.get('id') or 0)
    if not db.get_user(user_id):
        return web.json_response({'error': u'человека нет в базе'}, status=404)
    bot = request.app['bot']
    by = {'id': int(user['id']), 'full_name': user.get('first_name') or '',
          'username': user.get('username') or ''}
    if body.get('undo'):
        done, note = await blacklist.remove(bot, user_id, _Caller(by))
    else:
        done, note = await blacklist.add(bot, user_id, _Caller(by))
    return web.json_response({'ok': bool(done), 'note': note,
                              'banned': db.is_banned(user_id)})


class _Caller:
    u"""Тот, кто нажал в пульте, в том виде, в каком его ждёт bot/blacklist."""

    def __init__(self, who: dict):
        self.id = who['id']
        self.full_name = who.get('full_name') or u'Пульт'
        self.username = who.get('username') or None


# --------------------------------------------------------------- сервер

async def _index(request):
    return web.FileResponse(os.path.join(STATIC, 'index.html'),
                            headers={'Content-Type': TYPES['.html']})


# Тип файла задаём сами, а не отдаём на откуп окружению: в контейнере
# может не оказаться таблицы типов, тогда браузер получит «неизвестно
# что» и — из-за nosniff — не применит ни стили, ни скрипт.
TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


async def _static(request):
    u"""Отдать файл пульта из webapp/. Только имя файла, без путей вглубь."""
    name = request.match_info.get('name', '')
    if not name or '/' in name or '\\' in name or name.startswith('.'):
        raise web.HTTPNotFound()
    path = os.path.join(STATIC, name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    kind = TYPES.get(os.path.splitext(name)[1].lower())
    if not kind:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={'Content-Type': kind})


async def _health(request):
    u"""Жив ли пульт и есть ли чем перекодировать записи.

    ffmpeg ставится в контейнер отдельно (nixpacks.toml); если сборка его
    не подхватит, голосовые начнут уходить файлами — и понять это надо
    сразу, а не по жалобе команды.
    """
    return web.Response(text='ok; ffmpeg: %s' % (u'да' if record.have_ffmpeg() else u'нет'))


# Сколько запросов в минуту принимаем с одного адреса. Пульт открывают
# несколько человек, он сам подтягивает новое раз в 15 секунд — сотни
# запросов в минуту с одного адреса это уже не работа, а перебор.
RATE_LIMIT = 240
RATE_WINDOW = 60
_hits: dict = {}
_window_from = [0.0]


def _too_often(address: str, now: float | None = None) -> bool:
    u"""Считаем запросы окнами по минуте: на границе счётчик обнуляется."""
    now = time.time() if now is None else now
    if now - _window_from[0] > RATE_WINDOW:
        _window_from[0] = now
        _hits.clear()
    _hits[address] = _hits.get(address, 0) + 1
    return _hits[address] > RATE_LIMIT


@web.middleware
async def _guard_all(request, handler):
    u"""Общее для всех ответов: предел частоты и заголовки безопасности.

    Рамку (X-Frame-Options) намеренно не ставим: Telegram показывает
    мини-приложение внутри своего окна, и запрет сломал бы пульт в вебе.
    """
    if _too_often(request.remote or '?'):
        return web.json_response({'error': u'слишком часто, подождите минуту'}, status=429)
    response = await handler(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def build(bot) -> web.Application:
    # Пульт шлёт короткий JSON; мегабайтные тела по умолчанию ему не нужны
    # и только дают чужим лишний способ занять процесс бота.
    app = web.Application(middlewares=[_guard_all], client_max_size=64 * 1024)
    app['bot'] = bot
    app.router.add_get('/', _index)
    app.router.add_get('/health', _health)
    app.router.add_post('/api/people', api_people)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/send', api_send)
    app.router.add_post('/api/record', api_record)
    app.router.add_post('/api/edit', api_edit)
    app.router.add_post('/api/drop', api_drop)
    app.router.add_post('/api/ban', api_ban)
    app.router.add_get('/static/{name}', _static)
    return app


async def serve(bot) -> web.AppRunner | None:
    u"""Поднять пульт, если он нужен. None — порта нет, работаем без пульта."""
    if not config.WEB_PORT:
        return None
    runner = web.AppRunner(build(bot))
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', config.WEB_PORT).start()
    log.info(u'пульт админа поднят на порту %d, адрес для кнопки: %s',
             config.WEB_PORT, config.WEBAPP_URL or u'не задан (WEBAPP_URL)')
    return runner
