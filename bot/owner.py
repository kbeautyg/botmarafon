# -*- coding: utf-8 -*-
u"""Панель владельца — по секретному адресу, только для создателя проекта.

Рядом с «пультом» команды (bot/web.py): пульт открывают из Telegram, и
входит туда вся команда. Эта панель — для одного человека, Савелия.

Три замка подряд:
  1. Адрес. Панель живёт по секретному пути config.OWNER_PATH (от 16
     знаков). Не задан — маршрутов нет вовсе; старый /owner — обычный 404.
  2. Маскировка. По секретному адресу — то же, что aiohttp рисует на любом
     несуществующем адресе: «404: Not Found». Поля нет: пароль набирается
     вслепую и уходит по Enter. На пароль ответ всегда один — 404 и cookie
     ожидания, верный он или нет: по ответу правильность не понять.
  3. Код в Телеграм. Верный пароль — половина: бот пишет владельцу
     (config.OWNER_TG) шестизначный код — 5 минут, 3 попытки, не больше 3
     кодов за 10 минут. Без телефона владельца не войти.

Подбор: 5 неверных паролей с адреса — 15 минут пароль с него не
проверяется; 20 за час со всех адресов — дверь закрыта на час, владельцу
предупреждение. О каждом входе — сообщение. Состояние (коды, счётчики,
эпоха сессий) — в файле рядом с базой: переживает перезапуск.

Внутри: сводка, выгрузка данных и кода, копии базы (сделать, скачать,
удалить свои), журнал процесса, проверка Телеграма, перезапуск, история
входов, «выйти везде» и полная очистка.
"""
import asyncio
import base64
import collections
import glob
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import tempfile
import time
import zipfile

from aiohttp import web

from . import backup, config, db

log = logging.getLogger(__name__)

SESSION = '_s'
PENDING = '_p'
SESSION_TTL = 12 * 3600
PENDING_TTL = 5 * 60
CODE_TRIES = 3
IP_FAILS = 5
IP_WINDOW = 15 * 60
GLOBAL_FAILS = 20
GLOBAL_WINDOW = 3600
LOCK_FOR = 3600
ALERT_EVERY = 600
CODES_PER_WINDOW = 3
CODE_WINDOW = 600
LOG_MAX = 1024 * 1024
KEEP_BACKUPS = 20
CONFIRM_WORD = u'УДАЛИТЬ'
MAX_EXPORT = 300 * 1024 * 1024       # предел отдаваемого архива, 300 МБ
PATH_RE = re.compile(r'^/[A-Za-z0-9_-]{16,}$')
NONCE_RE = re.compile(r'^[A-Za-z0-9_-]{16,}$')
CODE_RE = re.compile(r'^[\d\s-]+$')
BACKUP_RE = re.compile(r'^marathon-\d{8}-\d{6}\.db$')
STARTED = time.time()

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'owner_panel.html')
# Медиа проекта (кружки, видео дней, отзывы). Отдельной константой, чтобы
# тесты подменяли её временной папкой и не трогали настоящие файлы.
MEDIA_DIR = os.path.join(config.ROOT, 'media')
# Что НЕ кладём в выгрузку кода: секреты, база, медиа, служебное, кэши.
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', 'data', 'media',
             '.venv', 'venv', 'node_modules', '.idea', '.vscode'}
SKIP_FILES = {'.env', '.coverage', '.git'}      # .git — файл-указатель у рабочих копий git worktree
SKIP_EXT = {'.pyc', '.pyo', '.db', '.db-wal', '.db-shm', '.log'}

# Ровно то, что aiohttp рисует на несуществующем адресе, — и невидимое поле.
DISGUISE = (
    u'<!DOCTYPE html><html><head><meta name="color-scheme" content="light dark">'
    u'<meta name="viewport" content="width=device-width"><meta name="robots" content="noindex,nofollow">'
    u'</head><body><pre style="word-wrap: break-word; white-space: pre-wrap;">404: Not Found</pre>'
    u'<input id="q" autocomplete="off" autocapitalize="off" spellcheck="false" aria-hidden="true" '
    u'tabindex="-1" style="position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;border:0;'
    u'padding:0;margin:0;font-size:16px;color:transparent;background:transparent;caret-color:transparent">'
    u'<script>(function(){var q=document.getElementById("q");q.type="pass"+"word";'
    u'var b=location.pathname.replace(/\\/+$/,"");'
    u'function f(){try{q.focus({preventScroll:true})}catch(e){q.focus()}}'
    u'["click","touchend"].forEach(function(t){document.addEventListener(t,f)});'
    u'document.addEventListener("keydown",function(){if(document.activeElement!==q)f()});'
    u'q.addEventListener("keydown",function(e){if(e.key!=="Enter")return;e.preventDefault();'
    u'var v=q.value;q.value="";if(!v)return;fetch(b+"/e",{method:"POST",'
    u'headers:{"Content-Type":"application/json"},body:JSON.stringify({v:v}),credentials:"same-origin"})'
    u'.then(function(r){if(r.status===200)location.reload()}).catch(function(){})});f()})();'
    u'</script></body></html>')

_exit = os._exit                     # тесты подменяют, чтобы «перезапуск» не гасил их
_tasks: set = set()


# ------------------------------------------------------------------ журнал процесса

class _Tail(logging.Handler):
    u"""Последние строки журнала процесса — на Railway файла журнала нет."""

    def __init__(self, size: int = 500):
        super().__init__()
        self.lines = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S'))

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:                # журнал — не повод ронять бота
            pass


_tail = _Tail()


# ------------------------------------------------------------------ включена ли

def prefix() -> str:
    raw = (config.OWNER_PATH or '').strip().rstrip('/')
    return raw if PATH_RE.match(raw) else ''


def enabled() -> bool:
    return bool(prefix() and config.OWNER_PASSWORD)


def describe() -> str:
    u"""Строка для журнала при запуске. Сам секретный адрес не печатаем."""
    if not (config.OWNER_PATH or '').strip():
        return u'панель владельца: выключена (не задан OWNER_PATH)'
    if not prefix():
        return u'панель владельца: ВЫКЛЮЧЕНА — OWNER_PATH короче 16 знаков или с недопустимыми символами'
    if not config.OWNER_PASSWORD:
        return u'панель владельца: выключена (пустой OWNER_PASSWORD)'
    return u'панель владельца: включена, вход — пароль' + (
        u' и код в Телеграм' if config.OWNER_2FA else u' (код выключен OWNER_2FA=off)')


# ------------------------------------------------------------------ подписи

def _key() -> bytes:
    u"""Ключ подписи выведен из токена бота: токен есть только у сервера."""
    return hashlib.sha256(b'owner-panel|' + config.BOT_TOKEN.encode()).digest()


def _mac(text: str) -> str:
    raw = hmac.new(_key(), text.encode('utf-8'), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _same(a: str, b: str) -> bool:
    u"""Сравнение без утечки по времени — и по длине: сравниваем отпечатки."""
    return hmac.compare_digest(_mac('=' + a), _mac('=' + b))


# ------------------------------------------------------------------ состояние

def _dir() -> str:
    return os.path.dirname(os.path.abspath(db.path() or config.DB_PATH))


def _state_path() -> str:
    return os.path.join(_dir(), '.owner-state.json')


def _numbers(values, now, window):
    return [t for t in (values or []) if isinstance(t, (int, float)) and now - t < window]


def _load(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        with open(_state_path(), encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    pending = raw.get('pending') if isinstance(raw.get('pending'), dict) else {}
    ip_fails = {}
    for ip, times in (raw.get('ip_fails') or {}).items():
        left = _numbers(times, now, IP_WINDOW)
        if left:
            ip_fails[ip] = left
    return {
        'epoch': raw.get('epoch') if isinstance(raw.get('epoch'), int) else 1,
        'pending': {n: r for n, r in pending.items() if isinstance(r, dict) and r.get('until', 0) > now},
        'fails': _numbers(raw.get('fails'), now, GLOBAL_WINDOW),
        'ip_fails': ip_fails,
        'codes': _numbers(raw.get('codes'), now, CODE_WINDOW),
        'lock_until': float(raw.get('lock_until') or 0),
        'alert_at': float(raw.get('alert_at') or 0),
    }


def _save(state: dict) -> None:
    path = _state_path()
    tmp = '%s.%d.tmp' % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as err:
        log.error(u'панель владельца: состояние не записано — %s', err)


def reset_limits() -> None:
    u"""Для тестов: забыть счётчики и коды, эпоху сессий не трогать."""
    state = _load()
    state.update(pending={}, fails=[], ip_fails={}, codes=[], lock_until=0, alert_at=0)
    _save(state)


# ------------------------------------------------------------------ история входов

def _log_path() -> str:
    return os.path.join(_dir(), '.owner-log.jsonl')


def _ip(request) -> str:
    u"""Адрес посетителя за прокси Railway: X-Real-IP, иначе последний адрес
    X-Forwarded-For — его дописал сам прокси, а не браузер."""
    real = request.headers.get('X-Real-IP', '').strip()
    if real:
        return real[:64]
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded.strip():
        return forwarded.split(',')[-1].strip()[:64]
    return request.remote or '?'


def _event(request, ev: str, info=None) -> None:
    line = {
        't': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'ev': ev,
        'ip': _ip(request) if request is not None else '',
        'ua': (request.headers.get('User-Agent', '') if request is not None else '')[:160],
    }
    if info:
        line['info'] = info
    path = _log_path()
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:
            with open(path, encoding='utf-8') as f:
                lines = f.read().splitlines()
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines[len(lines) // 2:]) + '\n')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(line, ensure_ascii=False) + '\n')
    except OSError:
        pass                              # журнал входов — не повод ломать вход


def history(limit: int = 150) -> list:
    try:
        with open(_log_path(), encoding='utf-8') as f:
            lines = f.read().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return list(reversed(out))


# ------------------------------------------------------------------ сообщения владельцу

def _chat():
    chat = (config.OWNER_TG or '').strip()
    return int(chat) if chat.lstrip('-').isdigit() else chat


async def _send(bot, text: str):
    if bot is None or not config.OWNER_TG:
        return False, u'не задан чат владельца (OWNER_TG)'
    try:
        await bot.send_message(_chat(), text, disable_web_page_preview=True)
        return True, ''
    except Exception as err:             # ошибка Телеграма — в журнал, не в ответ двери
        log.error(u'панель владельца: телеграм — %s', err)
        return False, str(err)[:200]


def _notify(request, text: str) -> None:
    u"""Отправка — фоном: ответ двери не ждёт Телеграма, и по времени ответа
    ничего не понять."""
    task = asyncio.get_running_loop().create_task(_send(request.app.get('bot'), text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ------------------------------------------------------------------ cookie и сессия

def _secure(request) -> bool:
    return (request.headers.get('X-Forwarded-Proto', '') == 'https' or request.secure
            or config.WEBAPP_URL.startswith('https://'))


def _cookie(request, name: str, value: str, max_age: int) -> str:
    out = '%s=%s; Path=%s; Max-Age=%d; HttpOnly; SameSite=Strict' % (name, value, prefix(), max_age)
    return out + ('; Secure' if _secure(request) else '')


def _session_value(issued: int, epoch: int) -> str:
    return '%d.%d.%s' % (issued, epoch, _mac('s|%d|%d' % (issued, epoch)))


def _pending_value(issued: int, nonce: str) -> str:
    return '%d.%s.%s' % (issued, nonce, _mac('p|%d|%s' % (issued, nonce)))


def logged_in(request) -> bool:
    value = request.cookies.get(SESSION, '')
    parts = value.split('.')
    if len(parts) != 3:
        return False
    try:
        issued, epoch = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if not _same(value, _session_value(issued, epoch)):
        return False
    if not 0 <= time.time() - issued <= SESSION_TTL:
        return False
    return epoch == _load()['epoch']


def _pending_nonce(request):
    value = request.cookies.get(PENDING, '')
    parts = value.split('.')
    if len(parts) != 3 or not NONCE_RE.match(parts[1]):
        return None
    try:
        issued = int(parts[0])
    except ValueError:
        return None
    if not _same(value, _pending_value(issued, parts[1])):
        return None
    return parts[1] if 0 <= time.time() - issued <= PENDING_TTL else None


def _not_found(cookie: str | None = None):
    u"""Ровно ответ aiohttp на несуществующий адрес, при нужде — с cookie."""
    return web.HTTPNotFound(headers={'Set-Cookie': cookie} if cookie else None)


def _same_origin(request) -> bool:
    origin = request.headers.get('Origin')
    if not origin:
        return True
    return origin.split('://', 1)[-1].rstrip('/') == request.host


def _welcome(request, epoch: int):
    response = web.json_response({'ok': True})
    response.headers.add('Set-Cookie', _cookie(request, SESSION, _session_value(int(time.time()), epoch), SESSION_TTL))
    response.headers.add('Set-Cookie', _cookie(request, PENDING, '', 0))
    response.headers['Cache-Control'] = 'no-store'
    return response


# ------------------------------------------------------------------ дверь

async def page(request):
    if logged_in(request):
        return web.FileResponse(PAGE, headers={
            'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
            'X-Robots-Tag': 'noindex, nofollow', 'X-Frame-Options': 'DENY'})
    return web.Response(status=404, text=DISGUISE, content_type='text/html',
                        headers={'Cache-Control': 'no-store'})


async def enter(request):
    u"""Пароль, затем код. Наружу — всегда 404, кроме верного кода."""
    if not _same_origin(request):
        raise _not_found()
    try:
        body = await request.json()
    except Exception:
        body = {}
    value = body.get('v', '') if isinstance(body, dict) else ''
    value = (value if isinstance(value, str) else '').strip()[:200]
    now = time.time()
    ip = _ip(request)
    state = _load(now)

    waiting = _pending_nonce(request)
    if waiting and waiting in state['pending']:
        rec = state['pending'][waiting]
        if not CODE_RE.match(value):      # не цифры — пароль набран ещё раз: попытку не тратим
            raise _not_found()
        digits = re.sub(r'\D', '', value)
        if digits and _same(_mac('c|%s|%s' % (waiting, digits)), rec.get('h', '')):
            del state['pending'][waiting]
            _save(state)
            _event(request, u'вход')
            _notify(request, u'✅ Вход в панель: марафон\nадрес %s\n%s' % (ip, request.headers.get('User-Agent', '')[:100]))
            return _welcome(request, state['epoch'])
        rec['tries'] = rec.get('tries', 0) + 1
        spent = rec['tries'] >= CODE_TRIES
        if spent:
            del state['pending'][waiting]
        _save(state)
        _event(request, u'неверный код, попытки кончились' if spent else u'неверный код')
        raise _not_found()

    nonce = secrets.token_urlsafe(18)
    hold = _cookie(request, PENDING, _pending_value(int(now), nonce), PENDING_TTL)
    if state['lock_until'] > now or len(state['ip_fails'].get(ip, [])) >= IP_FAILS:
        raise _not_found(hold)            # пароль даже не смотрим

    if not value or not _same(value, config.OWNER_PASSWORD):
        state['fails'].append(now)
        state['ip_fails'].setdefault(ip, []).append(now)
        _event(request, u'неверный пароль')
        if len(state['fails']) >= GLOBAL_FAILS:
            state['lock_until'] = now + LOCK_FOR
            _event(request, u'дверь закрыта на час')
            _notify(request, u'⛔ Панель «марафон» закрыта на час: %d неверных паролей за час. '
                             u'Последний — с адреса %s.' % (len(state['fails']), ip))
        elif now - state['alert_at'] > ALERT_EVERY:
            state['alert_at'] = now
            _notify(request, u'⚠️ Неверный пароль к панели «марафон» с адреса %s. За последний час: %d.'
                    % (ip, len(state['fails'])))
        _save(state)
        raise _not_found(hold)

    if not config.OWNER_2FA:
        _save(state)
        _event(request, u'вход без кода')
        _notify(request, u'✅ Вход в панель: марафон (без кода)\nадрес %s' % ip)
        return _welcome(request, state['epoch'])

    if len(state['codes']) >= CODES_PER_WINDOW:
        _save(state)
        _event(request, u'код не отправлен: слишком часто')
        raise _not_found(hold)

    code = '%06d' % secrets.randbelow(10 ** 6)
    state['pending'][nonce] = {'h': _mac('c|%s|%s' % (nonce, code)), 'until': now + PENDING_TTL,
                               'tries': 0, 'ip': ip}
    state['codes'].append(now)
    _save(state)
    _event(request, u'код отправлен')
    _notify(request, u'🔐 Код входа в панель «марафон»: %s\nДействует 5 минут. Запрос с адреса %s.\n'
                     u'Если входите не вы — пароль знает кто-то ещё: смените OWNER_PASSWORD.' % (code, ip))
    raise _not_found(hold)


def _api(handler):
    u"""Только с сессией и только со своей страницы; иначе — 404, как везде."""
    async def wrapped(request):
        if not logged_in(request):
            raise _not_found()
        if request.method == 'POST' and not _same_origin(request):
            raise _not_found()
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response
    return wrapped


async def _body(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# ------------------------------------------------------------------ сводка

async def overview(request):
    try:
        counts = {name: db._conn.execute('SELECT COUNT(*) FROM %s' % name).fetchone()[0]
                  for name in db.table_names()}
    except Exception as err:               # база могла быть не открыта
        log.exception(u'панель: сводка не собралась')
        return web.json_response({'error': str(err)[:300]}, status=500)
    state = _load()
    return web.json_response({
        'stats': db.stats(),
        'tables': counts,
        'db_bytes': _file_size(db.path()) + _file_size(db.path() + '-wal'),
        'media_bytes': _dir_size(MEDIA_DIR),
        'webapp_url': config.WEBAPP_URL,
        'backups': len(_backups()),
        'uptime': int(time.time() - STARTED),
        'door': _door(state),
    })


def _door(state: dict) -> dict:
    chat = config.OWNER_TG or ''
    return {
        'twoFactor': config.OWNER_2FA,
        'chat': (chat[:3] + u'…' + chat[-3:]) if chat else '',
        'sessionHours': SESSION_TTL // 3600,
        'lockedUntil': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(state['lock_until']))
        if state['lock_until'] > time.time() else None,
        'failsLastHour': len(state['fails']),
    }


# ------------------------------------------------------------------ выгрузки

async def export_data(request):
    _event(request, u'выгрузка данных')
    stamp = time.strftime('%Y%m%d-%H%M')
    with tempfile.TemporaryDirectory() as work:
        snapshot = os.path.join(work, 'marathon.db')
        backup.copy_db(db.path(), snapshot)   # согласованный слепок базы
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, 'marathon.db')
            for root, _dirs, files in os.walk(MEDIA_DIR):
                for name in files:
                    full = os.path.join(root, name)
                    zf.write(full, os.path.relpath(full, config.ROOT))
                    if buffer.tell() > MAX_EXPORT:
                        return web.json_response(
                            {'error': u'данных больше 300 МБ — выгрузите с сервера напрямую'}, status=413)
    return _archive(buffer, 'marathon-data-%s.zip' % stamp)


async def export_project(request):
    _event(request, u'выгрузка кода')
    stamp = time.strftime('%Y%m%d-%H%M')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(config.ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name in SKIP_FILES or name.startswith('.owner-') or \
                        os.path.splitext(name)[1].lower() in SKIP_EXT:
                    continue
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, config.ROOT))
                if buffer.tell() > MAX_EXPORT:
                    return web.json_response(
                        {'error': u'код больше 300 МБ — что-то лишнее попало в папку'}, status=413)
    return _archive(buffer, 'marathon-project-%s.zip' % stamp)


# ------------------------------------------------------------------ копии базы

def _backup_dir() -> str:
    return os.path.join(_dir(), 'owner-backups')


def _start_copies() -> list:
    base = db.path()
    return sorted(glob.glob(glob.escape(base) + '.before-*')) if base else []


def _backups() -> list:
    out = []
    for path in _start_copies():
        out.append(_backup_info(path, u'перед запуском'))
    for name in os.listdir(_backup_dir()) if os.path.isdir(_backup_dir()) else []:
        if BACKUP_RE.match(name):
            out.append(_backup_info(os.path.join(_backup_dir(), name), u'из панели'))
    return sorted(out, key=lambda b: b['mtime'], reverse=True)


def _backup_info(path: str, kind: str) -> dict:
    st = os.stat(path)
    return {'name': os.path.basename(path), 'bytes': st.st_size, 'kind': kind,
            'mtime': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime))}


def _backup_path(name: str) -> str | None:
    u"""Файл копии по имени — только из двух известных мест и без путей."""
    if not isinstance(name, str) or '/' in name or '\\' in name:
        return None
    if BACKUP_RE.match(name):
        path = os.path.join(_backup_dir(), name)
        return path if os.path.isfile(path) else None
    for path in _start_copies():
        if os.path.basename(path) == name:
            return path
    return None


async def backups_list(request):
    return web.json_response({'backups': _backups()})


async def backups_create(request):
    os.makedirs(_backup_dir(), exist_ok=True)
    name = 'marathon-%s.db' % time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    backup.copy_db(db.path(), os.path.join(_backup_dir(), name))
    mine = sorted(n for n in os.listdir(_backup_dir()) if BACKUP_RE.match(n))
    for old in mine[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(_backup_dir(), old))
        except OSError:
            pass
    _event(request, u'копия базы ' + name)
    return web.json_response({'name': name})


async def backups_get(request):
    path = _backup_path(request.query.get('name', ''))
    if not path:
        raise _not_found()
    return web.FileResponse(path, headers={
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': 'attachment; filename="%s"' % os.path.basename(path)})


async def backups_delete(request):
    name = (await _body(request)).get('name', '')
    if not isinstance(name, str) or not BACKUP_RE.match(name):
        return web.json_response({'error': u'удалять можно только копии из панели'}, status=400)
    path = os.path.join(_backup_dir(), name)
    if not os.path.isfile(path):
        return web.json_response({'error': u'такой копии нет'}, status=404)
    os.remove(path)
    _event(request, u'удалена копия ' + name)
    return web.json_response({'ok': True})


# ------------------------------------------------------------------ процесс

async def process_log(request):
    return web.json_response({'lines': list(_tail.lines)})


async def security(request):
    return web.json_response({'door': _door(_load()), 'history': history(150)})


async def tg_test(request):
    ok, error = await _send(request.app.get('bot'), u'🔔 Проверка связи из панели «марафон» — сообщения доходят.')
    _event(request, u'проверка телеграма' if ok else u'проверка телеграма не прошла')
    return web.json_response({'ok': ok, 'error': error}, status=200 if ok else 502)


async def restart(request):
    _event(request, u'перезапуск')
    # ответ успевает уйти; Railway (restartPolicyType = ALWAYS) поднимет бота заново
    asyncio.get_running_loop().call_later(0.5, _exit, 0)
    return web.json_response({'ok': True})


async def logout(request):
    _event(request, u'выход')
    response = web.json_response({'ok': True})
    response.headers.add('Set-Cookie', _cookie(request, SESSION, '', 0))
    return response


async def logout_all(request):
    state = _load()
    state['epoch'] += 1
    _save(state)
    _event(request, u'выход на всех устройствах')
    return web.json_response({'ok': True})


# ------------------------------------------------------------------ полная очистка

async def wipe(request):
    if (await _body(request)).get('confirm', '') != CONFIRM_WORD:
        return web.json_response(
            {'error': u'для очистки пришлите слово %s в поле confirm' % CONFIRM_WORD}, status=400)
    было = db.wipe_all()
    files = _clear_media() + _clear_db_copies()
    log.warning(u'панель владельца: данные стёрты, строк было %s, файлов удалено %d', было, files)
    _event(request, u'все данные стёрты', было)
    _notify(request, u'🧨 В панели «марафон» стёрты все данные: строк %d, файлов %d.'
            % (sum(было.values()), files))
    return web.json_response({'wiped': было, 'media_files': files})


# ------------------------------------------------------------------ мелочи

def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except (OSError, TypeError):
        return 0


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += _file_size(os.path.join(root, name))
    return total


def _clear_media() -> int:
    u"""Удалить файлы медиа (кружки, отзывы, обложки). Папки оставляем."""
    removed = 0
    for root, _dirs, files in os.walk(MEDIA_DIR):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
                removed += 1
            except OSError:
                pass
    return removed


def _clear_db_copies() -> int:
    u"""Копии базы — и «перед запуском», и сделанные из панели: в них те же
    данные участников, без этого «полная очистка» оставила бы их на диске."""
    removed = 0
    targets = _start_copies()
    if os.path.isdir(_backup_dir()):
        targets += [os.path.join(_backup_dir(), n) for n in os.listdir(_backup_dir())]
    for path in targets:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def _archive(buffer: io.BytesIO, filename: str):
    return web.Response(body=buffer.getvalue(), headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="%s"' % filename,
    })


def register(app: web.Application) -> None:
    u"""Подключить маршруты панели к серверу пульта — только если она включена."""
    log.info(describe())
    if not enabled():
        return
    root = logging.getLogger()
    if _tail not in root.handlers:
        root.addHandler(_tail)
    pre = prefix()
    app.router.add_get(pre, page)
    app.router.add_get(pre + '/', page)
    app.router.add_post(pre + '/e', enter)
    for method, path, handler in (
            ('GET', '/api/overview', overview),
            ('GET', '/api/export/data', export_data),
            ('GET', '/api/export/project', export_project),
            ('GET', '/api/backups', backups_list),
            ('POST', '/api/backups', backups_create),
            ('GET', '/api/backups/get', backups_get),
            ('POST', '/api/backups/delete', backups_delete),
            ('GET', '/api/log', process_log),
            ('GET', '/api/security', security),
            ('POST', '/api/tg-test', tg_test),
            ('POST', '/api/restart', restart),
            ('POST', '/api/logout', logout),
            ('POST', '/api/logout-all', logout_all),
            ('POST', '/api/wipe', wipe)):
        app.router.add_route(method, pre + path, _api(handler))
