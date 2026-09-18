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
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

from aiohttp import web

from . import blacklist, config, db, delivery

log = logging.getLogger(__name__)

STATIC = os.path.join(config.ROOT, 'webapp')
# Сколько живёт подпись Telegram. Сутки: пульт держат открытым весь день,
# а протухшую подпись мини-приложение обновляет само при следующем входе.
MAX_AGE = 24 * 3600
PAGE_SIZE = 60
# Предел сообщения у Telegram — 4096 знаков; режем чуть раньше, чтобы
# человеку в пульте пришёл понятный отказ, а не ошибка от Telegram.
MAX_TEXT = 4000
# Сколько чатов Павла опрашивать для карточки: каждый — отдельный
# запрос к Telegram, а карточка должна открываться сразу.
CHATS_IN_CARD = 8


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


def _messages(user_id: int) -> list[dict]:
    u"""Переписка для пульта: только то, что он показывает.

    Внутренние id отправителя и вложения наружу не отдаём — пульту они не
    нужны, а любое лишнее поле в ответе рано или поздно где-нибудь всплывёт.
    """
    return [{'id': m['id'], 'kind': m['kind'], 'text': m['text'], 'at': m['at'],
             'mine': m['side'] == 'out'} for m in db.chat_history(user_id)]


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


@route
async def api_send(request, user):
    u"""Отправить человеку сообщение от имени бота."""
    body = await _body(request)
    user_id = int(body.get('id') or 0)
    text = str(body.get('text') or '').strip()
    if not text:
        return web.json_response({'error': u'пустое сообщение'}, status=400)
    if len(text) > MAX_TEXT:
        return web.json_response(
            {'error': u'слишком длинное: %d знаков, влезает %d' % (len(text), MAX_TEXT)},
            status=400)
    person = db.get_user(user_id)
    if not person:
        return web.json_response({'error': u'человека нет в базе'}, status=404)

    bot = request.app['bot']
    try:
        await delivery._guard(bot.send_message(user_id, text, parse_mode=None))
    except delivery.Gone:
        db.mark_blocked(user_id)
        return web.json_response({'error': u'человек закрыл бота — писать ему нельзя'}, status=409)
    db.save_message(user_id, 'out', 'text', text, author=int(user['id']))
    log.info(u'пульт: %s написал(а) человеку %s', user['id'], user_id)
    return web.json_response({'messages': _messages(user_id)})


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
    return web.Response(text='ok')


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
