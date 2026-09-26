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

from . import blacklist, broadcast, chatlog, config, db, delivery, record

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
# Файл с телефона (AleX 23.09.2026: «прикрепить картинку, видео, аудио с
# устройства»). Телеграм берёт от бота до пятидесяти мегабайт — просим
# меньше, чтобы отказ пришёл от нас и по-человечески, а не от Телеграма.
MAX_FILE = 45 * 1024 * 1024
# Фотографией Телеграм принимает только до десяти мегабайт. Что тяжелее —
# уходит файлом: лучше так, чем отказ на ровном месте.
MAX_PHOTO = 10 * 1024 * 1024
# Длиннее Телеграм подпись к файлу не берёт. Обрезать молча нельзя:
# человек бы даже не узнал, что конец его текста никуда не ушёл.
MAX_CAPTION = 1024
# Скольких можно вычеркнуть из группы руками. Больше — это уже не
# «снять тех, кому уже ушло», а другая группа.
MAX_SKIPPED = 2000


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
        'last_mass': bool(row.get('last_mass')),
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
    offset = max(0, min(int(body.get('offset') or 0), 100000))
    # Выбрана группа — показываем её, и только её: тогда галочки в списке
    # значат ровно то, что значат, и снять их можно у кого угодно из неё
    # (AleX 23.09.2026).
    scope = wanted_scope(body)
    rows = db.people(query, PAGE_SIZE, only_chats=only_chats, offset=offset, scope=scope)
    everyone = False
    if only_chats and not scope and not rows and not query.strip() and not offset:
        rows = db.people('', PAGE_SIZE, only_chats=False)
        everyone = bool(rows)
        only_chats = not everyone
    total = db.people_count(query, only_chats=only_chats, scope=scope)
    return web.json_response({
        'people': [_person(r) for r in rows],
        'everyone': everyone,
        'total': total,
        # Сколько ещё осталось за экраном: пульт рисует «показать ещё»
        # только когда есть что показывать (AleX 23.09.2026).
        'more': max(0, total - offset - len(rows)),
    })


def wanted_scope(source) -> str:
    u"""Какую группу выбрали в пульте. Пусто — не группу, а людей поимённо."""
    scope = str(source.get('scope') or '').strip()
    return scope if scope in db.AUDIENCES else ''


def skipped_ids(source) -> list:
    u"""У кого сняли галочку в группе — этим не шлём.

    AleX 23.09.2026: «выбрать всех, а потом рукой убрать галочку у тех,
    кому уже отправилось, — чтобы дважды одно и то же не отправлять».
    """
    raw = source.get('except')
    if isinstance(raw, str):
        raw = raw.split(',')
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_SKIPPED]:
        try:
            out.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    return out


# Как назвать группу людям. Дни — это где человек сейчас: получил свой
# день и не получил следующий, — поэтому «на 2 дне», а не «дошёл до 2».
SCOPES = (
    ('all', u'Все участники'),
    ('day1', u'Сейчас на 1 дне'),
    ('day2', u'Сейчас на 2 дне'),
    ('day3', u'Сейчас на 3 дне'),
    ('day4', u'Дошли до 4 дня'),
    ('bought', u'Нажали «Купить»'),
)


@route
async def api_scopes(request, user):
    u"""Группы для кнопок «кому писать» и сколько в каждой человек.

    AleX 23.09.2026: «пусть сверху будет галочка выбрать всех… или
    оповестить всех, кто на первом, втором, третьем, четвёртом дне или
    нажал купить».
    """
    counts = db.audience_counts()
    return web.json_response({'scopes': [
        {'key': key, 'title': title, 'count': counts.get(key, 0)}
        for key, title in SCOPES]})


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


# С 20.09.2026 кнопки «Да»/«Нет» уходят прямо под записью дня, и момент
# вопроса пишется вместе с ней (delivery.open_poll) — на миллисекунды раньше
# самой записи. В ленте это читалось задом наперёд: «бот спросил, посмотрел
# ли первый день», а строкой ниже — «бот прислал запись первого дня» (AleX
# 21.09.2026, скриншот диалога). Вопрос, совпавший с записью, — это кнопки
# под ней, отдельной строкой его не показываем. Остаётся напоминание,
# пришедшее позже, — его и правда присылали отдельно.
SAME_MOMENT = 120


def _steps(user_id: int) -> list[dict]:
    u"""Шаги воронки для ленты — без вопроса, ушедшего вместе с записью."""
    steps = db.timeline(user_id)
    days = {}
    for step in steps:
        if step['kind'] == 'day':
            days.setdefault(step['ref'], []).append(step['at'])
    out = []
    for step in steps:
        if step['kind'] == 'poll':
            sent = days.get(step['ref'].replace('day', ''), ())
            if any(abs(step['at'] - at) <= SAME_MOMENT for at in sent):
                continue
        out.append(step)
    return out


# Что из присланного человеком пульт покажет прямо в диалоге. Остальное —
# видео, голосовые, кружки, документы — бот пришлёт админу в личку: там
# телеграм откроет что угодно и любого размера, а пульту для этого пришлось
# бы качать файл целиком через себя.
IMAGE_TYPES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
               '.webp': 'image/webp', '.gif': 'image/gif'}
# Больше этого Telegram боту файл не отдаёт вовсе (Bot API, getFile).
MAX_VIEW = 20 * 1024 * 1024


def _messages(user_id: int) -> list[dict]:
    u"""Лента диалога: сообщения вперемешку с шагами воронки, по времени.

    Внутренние id отправителя и вложения наружу не отдаём — пульту они не
    нужны, а любое лишнее поле в ответе рано или поздно где-нибудь всплывёт.
    Про вложение пульт знает одно: есть ли оно (file) — открыть его можно
    только по номеру сообщения, через /api/file.
    """
    lines = [{'id': m['id'], 'kind': m['kind'], 'text': m['text'], 'at': m['at'],
              'mine': m['side'] == 'out',
              'file': bool(m.get('file_id') and not str(m['file_id']).startswith('http')),
              # править и удалять можно только своё и только текстовое. Общее
              # (рассылка, эфир, дожим) не правим: правка простым текстом сняла
              # бы с него кнопки и ссылки — удалить у человека можно.
              'can_edit': bool(m['side'] == 'out' and m['tg_id'] and m['kind'] == 'text'
                               and not m.get('mass')),
              'can_drop': bool(m['side'] == 'out' and m['tg_id']),
              'mass': bool(m.get('mass'))}
             for m in db.chat_history(user_id)]
    lines += [{'id': 0, 'note': True, 'text': _note(step), 'at': step['at'],
               'kind': 'text', 'mine': False} for step in _steps(user_id)]
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
        if not user_id or user_id in out or not db.get_user(user_id):
            continue
        # Чёрный список значит «бот с ним больше не работает»: его обходят и
        # дожим, и рассылка. Галочка в списке — не повод сделать исключение.
        if db.is_banned(user_id):
            continue
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


def scope_title(scope: str) -> str:
    return dict(SCOPES).get(scope, scope)


async def spread(request, user, scope: str, compose, skip=()) -> web.Response:
    u"""Отправить одно и то же целой группе — через обычную рассылку.

    Группа — это сотни человек и минуты отправки: держать ради этого
    открытым запрос из пульта нельзя. Поэтому собираем сообщение у автора
    в личке и отдаём его движку рассылки (bot/broadcast.py): он идёт с
    нужной скоростью, ведёт счёт, продолжает после перезапуска и в конце
    докладывает. Заодно автор видит у себя ровно то, что ушло людям.
    """
    ids = db.audience(scope)
    if not ids:
        return web.json_response({'error': u'в этой группе сейчас никого'}, status=400)
    if skip:
        ids = [user_id for user_id in ids if user_id not in set(skip)]
        if not ids:
            return web.json_response(
                {'error': u'галочки сняты у всех — писать некому'}, status=400)
    author = int(user['id'])
    try:
        origin = await compose(author)
    except delivery.Gone:
        return web.json_response(
            {'error': u'напишите боту /start в личке — рассылку он собирает у вас'},
            status=409)
    message_id = getattr(origin, 'message_id', None)
    if not message_id:
        return web.json_response({'error': u'не смог собрать сообщение'}, status=502)
    # «Все» — это и есть обычная рассылка: списком её не перечисляем, иначе
    # в базу лёг бы километровый перечень id. Но если галочки сняты, «все»
    # уже не все — тогда только списком: 26.09.2026 снятые в «Все участники»
    # получали сообщение наравне с остальными.
    targets = None if scope == 'all' and not skip else ids
    task = db.broadcast_add(author, message_id, author, targets, *chatlog.parts(origin))
    asyncio.create_task(broadcast.run(request.app['bot'], task))
    log.info(u'пульт: %s шлёт группе «%s» — %d чел.', author, scope, len(ids))
    return web.json_response({
        'started': True, 'count': len(ids), 'scope': scope,
        'title': scope_title(scope),
        'minutes': max(1, int(len(ids) * broadcast.PAUSE / 60 + 0.5)),
    })


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
    scope = wanted_scope(body)
    if scope:
        async def compose(uid):
            if media:
                return (await send_media(bot, uid, media, text))[1]
            return await delivery._guard(bot.send_message(uid, text, parse_mode=None))

        return await spread(request, user, scope, compose, skipped_ids(body))

    chosen = picked_ids(body)
    if chosen:
        async def one(uid):
            if media:
                kind, sent = await send_media(bot, uid, media, text)
            else:
                kind = 'text'
                sent = await delivery._guard(bot.send_message(uid, text, parse_mode=None))
            # нескольким разом — общее, а не личный ответ: из «ждут» не убирает
            db.save_message(uid, 'out', kind, text or media, media or None,
                            author=int(user['id']), tg_id=getattr(sent, 'message_id', None),
                            mass=len(chosen) > 1)

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


# Каким вызовом слать вложение по его виду. Всё уходит по file_id: сам
# файл через пульт не едет, и размер не важен — это тот же файл, что уже
# лежит у Telegram.
SEND_BY_KIND = {
    'photo': ('send_photo', 'photo'),
    'video': ('send_video', 'video'),
    'video_note': ('send_video_note', 'video_note'),
    'voice': ('send_voice', 'voice'),
    'audio': ('send_audio', 'audio'),
    'document': ('send_document', 'document'),
    'sticker': ('send_sticker', 'sticker'),
    'animation': ('send_animation', 'animation'),
}


@route
async def api_file(request, user):
    u"""Открыть вложение из переписки (AleX 21.09.2026: «человек скинул какой-то
    файл в переписке с ботом, как посмотреть что это?»).

    how='view' — картинку отдаём прямо в пульт. how='me' — бот присылает
    вложение в личку тому, кто нажал: там телеграм откроет любое.

    Открываем только по номеру сообщения из нашей же базы, а не по file_id
    из запроса: пульт не должен уметь вытащить из бота чужое вложение.
    """
    body = request['body']
    line = db.message(int(body.get('id') or 0))
    file_id = (line or {}).get('file_id') or ''
    if not line or line.get('gone') or not file_id or file_id.startswith('http'):
        return web.json_response({'error': u'вложения в этом сообщении нет'}, status=404)
    bot = request.app['bot']

    if body.get('how') == 'view':
        if line['kind'] not in ('photo', 'document'):
            return web.json_response({'error': u'это не картинка'}, status=415)
        info = await bot.get_file(file_id)
        kind = IMAGE_TYPES.get(os.path.splitext(info.file_path or '')[1].lower())
        if not kind or (info.file_size or 0) > MAX_VIEW:
            return web.json_response({'error': u'это не картинка'}, status=415)
        data = await bot.download_file(info.file_path)
        raw = data.read() if hasattr(data, 'read') else data
        log.info(u'пульт: %s смотрит вложение сообщения %s', user['id'], line['id'])
        return web.Response(body=raw, content_type=kind,
                            headers={'Cache-Control': 'private, no-store'})

    method, field = SEND_BY_KIND.get(line['kind'], ('send_document', 'document'))
    person = db.get_user(line['user_id']) or {}
    who = person.get('first_name') or u'человек'
    if person.get('username'):
        who += u' @' + person['username']
    head = u'📎 Вложение от %s (ID %s), %s' % (
        who, line['user_id'], time.strftime('%d.%m %H:%M', time.localtime(line['at'])))
    if line.get('text'):
        head += u'\n\n' + line['text']
    admin = int(user['id'])
    try:
        await bot.send_message(admin, head, parse_mode=None)
        await getattr(bot, method)(admin, **{field: file_id})
    except Exception as err:
        log.warning(u'пульт: вложение %s не переслали %s: %s', line['id'], admin, err)
        return web.json_response(
            {'error': u'не смог прислать: напишите боту /start в личке и попробуйте снова'},
            status=502)
    log.info(u'пульт: вложение сообщения %s прислали %s', line['id'], admin)
    return web.json_response({'sent': True})


@route
async def api_edit(request, user):
    u"""Поправить своё отправленное сообщение — и у человека тоже."""
    body = await _body(request)
    line = db.message(int(body.get('messageId') or 0))
    text = str(body.get('text') or '').strip()
    if not line or line['side'] != 'out' or not line['tg_id'] or line.get('mass'):
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
            # Предел свой у записи и свой у файла с телефона; вид приходит
            # полем kind — пульт шлёт его раньше самого файла.
            big = fields.get('kind') == 'file'
            limit = MAX_FILE if big else MAX_UPLOAD
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                blob += chunk
                if len(blob) > limit:
                    return web.json_response({'error': (
                        u'файл больше %d МБ — столько Телеграм от бота не принимает'
                        % (limit // (1024 * 1024)) if big else
                        u'запись длиннее, чем бот принимает — запишите короче')},
                        status=413)
        else:
            fields[part.name] = (await part.read(decode=True)).decode('utf-8', 'replace')

    user = _who_safe(fields)
    if not user:
        return web.json_response({'error': u'этот пульт — для команды проекта'}, status=403)
    if not blob:
        return web.json_response({'error': u'пустая запись'}, status=400)

    user_id = int(fields.get('id') or 0)
    kind = fields.get('kind') if fields.get('kind') in ('note', 'file') else 'voice'
    caption = (fields.get('text') or '').strip()
    if len(caption) > MAX_CAPTION:
        return web.json_response({'error': (
            u'подпись длиннее %d знаков — столько Телеграм к файлу не берёт. '
            u'Длинный текст лучше отправить отдельным сообщением.' % MAX_CAPTION)},
            status=400)

    # Картинка, видео или аудио прямо с телефона — их не перекодируют, у
    # них своя дорога (AleX 23.09.2026).
    if kind == 'file':
        return await _file_out(request, user, fields, blob, name, caption)

    chosen = picked_ids(fields)
    scope = wanted_scope(fields)
    if chosen or scope:
        bot = request.app['bot']
        # Перекодируем один раз на всех: ffmpeg на каждого из двадцати —
        # это полминуты ожидания на ровном месте.
        try:
            ready, saved = await _make_record(kind, blob, name)
        except Exception as err:
            log.exception(u'пульт: запись выбранным не собралась')
            return web.json_response({'error': str(err)[:300]}, status=500)
        try:
            if scope:
                return await spread(request, user, scope, lambda uid: _put_record(
                    bot, uid, saved, ready, name, caption), skipped_ids(fields))
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


# Чем отправлять файл с телефона. Телеграм различает картинку, видео,
# музыку и всё прочее: от этого зависит, увидит человек фотографию или
# значок файла, который ещё надо скачать.
FILE_BY_MIME = (('image/gif', 'animation'), ('image/', 'photo'),
                ('video/', 'video'), ('audio/', 'audio'))
FILE_BY_EXT = {
    '.jpg': 'photo', '.jpeg': 'photo', '.png': 'photo', '.webp': 'photo',
    '.gif': 'animation',
    '.mp4': 'video', '.mov': 'video', '.m4v': 'video', '.webm': 'video',
    '.mp3': 'audio', '.m4a': 'audio', '.ogg': 'audio', '.wav': 'audio', '.aac': 'audio',
}


def file_kind(mime: str, name: str, size: int = 0) -> str:
    u"""Чем отправлять: фотографией, видео, музыкой или файлом.

    Смотрим на то, что сказал телефон, а если он промолчал — на
    расширение. Незнакомое уходит файлом: так дойдёт что угодно.
    """
    head = (mime or '').split(';')[0].strip().lower()
    kind = ''
    for start, how in FILE_BY_MIME:
        if head.startswith(start):
            kind = how
            break
    if not kind:
        kind = FILE_BY_EXT.get(os.path.splitext(name or '')[1].lower(), 'document')
    # Фотографией Телеграм берёт только до десяти мегабайт. Что тяжелее —
    # отправляем файлом: так дойдёт целиком и без пережатия.
    if kind == 'photo' and size > MAX_PHOTO:
        return 'document'
    return kind


async def _send_file(bot, user_id: int, kind: str, body, name: str, caption: str):
    u"""Файл одному человеку. body — байты или file_id уже залитого файла."""
    from aiogram.types import BufferedInputFile

    method, field = SEND_BY_KIND.get(kind, ('send_document', 'document'))
    ready = body if isinstance(body, str) else BufferedInputFile(body, name or 'file')
    keys = {field: ready}
    if kind != 'video_note':
        keys['caption'] = caption or None
    return await delivery._guard(getattr(bot, method)(user_id, **keys))


def _file_id(sent, kind: str) -> str:
    u"""file_id только что отправленного — чтобы не заливать файл заново."""
    piece = getattr(sent, kind, None)
    if kind == 'photo':
        piece = piece[-1] if piece else None     # самый крупный из размеров
    return getattr(piece, 'file_id', '') or ''


async def _file_out(request, user, fields: dict, blob: bytes, name: str, caption: str):
    u"""Файл с устройства: одному, выбранным или целой группе."""
    bot = request.app['bot']
    author = int(user['id'])
    kind = file_kind(fields.get('mime') or '', name, len(blob))

    scope = wanted_scope(fields)
    if scope:
        return await spread(request, user, scope,
                            lambda uid: _send_file(bot, uid, kind, blob, name, caption),
                            skipped_ids(fields))

    chosen = picked_ids(fields)
    if chosen:
        # Заливаем один раз — себе, — и остальным уходит тот же самый файл
        # по его номеру у Телеграма: сорок мегабайт двадцать раз подряд не
        # нужны ни нам, ни телефону.
        shared, saved_id = blob, ''
        try:
            first = await _send_file(bot, author, kind, blob, name, caption)
            saved_id = _file_id(first, kind)
            shared = saved_id or blob
        except Exception as err:
            log.warning(u'пульт: файл не лёг автору %s: %s', author, err)

        async def one(uid):
            sent = await _send_file(bot, uid, kind, shared, name, caption)
            db.save_message(uid, 'out', kind, caption or None, saved_id or None,
                            author, getattr(sent, 'message_id', None))

        log.info(u'пульт: %s шлёт %s %d выбранным', author, kind, len(chosen))
        return await to_each(chosen, one)

    user_id = int(fields.get('id') or 0)
    if not db.get_user(user_id):
        return web.json_response({'error': u'человека нет в базе'}, status=404)
    try:
        sent = await _send_file(bot, user_id, kind, blob, name, caption)
    except delivery.Gone:
        db.mark_blocked(user_id)
        return web.json_response({'error': u'человек закрыл бота — писать ему нельзя'},
                                 status=409)
    db.save_message(user_id, 'out', kind, caption or None, _file_id(sent, kind) or None,
                    author, getattr(sent, 'message_id', None))
    log.info(u'пульт: %s отправил(а) %s человеку %s', author, kind, user_id)
    return web.json_response({'messages': _messages(user_id), 'kind': kind})


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
    sent = await _put_record(bot, user_id, saved, ready, name, caption)
    db.save_message(user_id, 'out', saved, caption or None, None, author,
                    getattr(sent, 'message_id', None))
    log.info(u'пульт: %s отправил(а) %s человеку %s', author, saved, user_id)
    return sent


async def _put_record(bot, user_id: int, saved: str, ready, name: str, caption: str):
    u"""Отправить готовую запись — и всё. В переписку её кладёт _push_record,
    а рассылке группе она там ни к чему: людей сотни, а сообщение одно."""
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
    app.router.add_post('/api/scopes', api_scopes)
    app.router.add_post('/api/chat', api_chat)
    app.router.add_post('/api/send', api_send)
    app.router.add_post('/api/record', api_record)
    app.router.add_post('/api/edit', api_edit)
    app.router.add_post('/api/drop', api_drop)
    app.router.add_post('/api/ban', api_ban)
    app.router.add_post('/api/file', api_file)
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
