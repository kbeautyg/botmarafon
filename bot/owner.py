# -*- coding: utf-8 -*-
u"""Панель владельца: отдельная веб-страница /owner только для создателя проекта.

Это вторая админка рядом с «пультом» команды (bot/web.py). Пульт открывают
из Telegram, и входит туда вся команда — подпись Telegram, config.is_team.
Эта панель другая: обычная веб-страница с входом по паролю, для одного
человека — Савелия. Отсюда он видит сводку по проекту, выгружает данные,
выгружает код и может стереть все данные начисто.

Дверь. Панель висит на том же публичном адресе, что и пульт, поэтому:
  • вход — по паролю config.OWNER_PASSWORD (сравнение постоянное по времени);
  • после входа — подписанная cookie (ключ выведен из токена бота), HttpOnly,
    на проде ещё и Secure; живёт SESSION_TTL;
  • у входа свой строгий лимит попыток с коротким замком, чтобы пароль нельзя
    было подобрать перебором;
  • выгрузка кода не кладёт в архив ни .env, ни базу, ни .git — только исходники;
  • очистка данных требует входа и явного слова-подтверждения в теле запроса.

Пустой OWNER_PASSWORD выключает панель целиком: все её маршруты отвечают 404,
как будто её нет.
"""
import glob
import hashlib
import hmac
import io
import logging
import os
import tempfile
import time
import zipfile

from aiohttp import web

from . import backup, config, db

log = logging.getLogger(__name__)

COOKIE = 'owner_session'
SESSION_TTL = 7 * 24 * 3600          # неделю не переспрашиваем пароль
# Вход: не больше стольких попыток за окно с одного адреса, дальше — замок.
# Для перебора шестибуквенного пароля этого хватает с огромным запасом.
LOGIN_TRIES = 8
LOGIN_WINDOW = 600                    # 10 минут
CONFIRM_WORD = u'УДАЛИТЬ'             # слово-подтверждение полной очистки
MAX_EXPORT = 300 * 1024 * 1024       # предел отдаваемого архива, 300 МБ

STATIC = os.path.join(config.ROOT, 'webapp')
# Медиа проекта (кружки, видео дней, отзывы). Отдельной константой, чтобы
# тесты подменяли её временной папкой и не трогали настоящие файлы.
MEDIA_DIR = os.path.join(config.ROOT, 'media')
# Что НЕ кладём в выгрузку кода: секреты, база, медиа, служебное, кэши.
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', 'data', 'media',
             '.venv', 'venv', 'node_modules', '.idea', '.vscode'}
SKIP_FILES = {'.env', '.coverage'}
SKIP_EXT = {'.pyc', '.pyo', '.db', '.db-wal', '.db-shm', '.log'}

_login_hits: dict = {}
_login_from = [0.0]


# ------------------------------------------------------------------ включена ли

def enabled() -> bool:
    u"""Панель работает, только если задан пароль."""
    return bool(config.OWNER_PASSWORD)


def _guard_off():
    u"""Панель выключена — ведём себя так, будто маршрута нет."""
    raise web.HTTPNotFound()


# ------------------------------------------------------------------ сессия

def _key() -> bytes:
    u"""Ключ подписи cookie. Выведен из токена бота — отдельный секрет не нужен,
    а токен и так есть только у сервера."""
    return hashlib.sha256(b'owner-panel|' + config.BOT_TOKEN.encode()).digest()


def _sign(issued: int) -> str:
    mac = hmac.new(_key(), str(issued).encode(), hashlib.sha256).hexdigest()
    return u'%d.%s' % (issued, mac)


def _valid(value: str, now: float) -> bool:
    try:
        issued_s, mac = value.split('.', 1)
        issued = int(issued_s)
    except (ValueError, AttributeError):
        return False
    good = hmac.new(_key(), str(issued).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return False
    return 0 <= now - issued <= SESSION_TTL


def _logged_in(request) -> bool:
    return _valid(request.cookies.get(COOKIE, ''), time.time())


def _secure() -> bool:
    u"""Secure-cookie — на проде (https). Локально по http её бы не вернул браузер."""
    return config.WEBAPP_URL.startswith('https://')


def _set_cookie(response, issued: int) -> None:
    response.set_cookie(COOKIE, _sign(issued), max_age=SESSION_TTL, httponly=True,
                        samesite='Lax', secure=_secure(), path='/owner')


def _require(request) -> None:
    if not _logged_in(request):
        raise web.HTTPUnauthorized(text=u'нужен вход в панель')


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


# ------------------------------------------------------------------ лимит входа

def _login_locked(address: str, now: float | None = None) -> bool:
    u"""Исчерпаны ли попытки входа с адреса за окно."""
    now = time.time() if now is None else now
    if now - _login_from[0] > LOGIN_WINDOW:
        _login_from[0] = now
        _login_hits.clear()
    return _login_hits.get(address, 0) >= LOGIN_TRIES


def _login_miss(address: str) -> None:
    _login_hits[address] = _login_hits.get(address, 0) + 1


# ------------------------------------------------------------------ страница

async def page(request):
    if not enabled():
        _guard_off()
    path = os.path.join(STATIC, 'owner.html')
    return _no_store(web.FileResponse(path, headers={'Content-Type': 'text/html; charset=utf-8'}))


# ------------------------------------------------------------------ вход/выход

async def login(request):
    if not enabled():
        _guard_off()
    address = request.remote or '?'
    if _login_locked(address):
        return _no_store(web.json_response(
            {'error': u'слишком много попыток — подождите 10 минут'}, status=429))
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = body.get('password', '') if isinstance(body, dict) else ''
    if not isinstance(password, str) or not hmac.compare_digest(password, config.OWNER_PASSWORD):
        _login_miss(address)
        return _no_store(web.json_response({'error': u'неверный пароль'}, status=403))
    response = web.json_response({'ok': True})
    _set_cookie(response, int(time.time()))
    return _no_store(response)


async def logout(request):
    response = web.json_response({'ok': True})
    response.del_cookie(COOKIE, path='/owner')
    return _no_store(response)


# ------------------------------------------------------------------ сводка

async def overview(request):
    if not enabled():
        _guard_off()
    _require(request)
    try:
        counts = {name: db._conn.execute('SELECT COUNT(*) FROM %s' % name).fetchone()[0]
                  for name in db.table_names()}
    except Exception as err:               # база могла быть не открыта
        log.exception(u'панель: сводка не собралась')
        return _no_store(web.json_response({'error': str(err)[:300]}, status=500))
    data = {
        'stats': db.stats(),
        'tables': counts,
        'db_bytes': _file_size(db.path()) + _file_size(db.path() + '-wal'),
        'media_bytes': _dir_size(MEDIA_DIR),
        'webapp_url': config.WEBAPP_URL,
    }
    return _no_store(web.json_response(data))


# ------------------------------------------------------------------ выгрузка данных

async def export_data(request):
    if not enabled():
        _guard_off()
    _require(request)
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
                    rel = os.path.relpath(full, config.ROOT)
                    zf.write(full, rel)
                    if buffer.tell() > MAX_EXPORT:
                        return _no_store(web.json_response(
                            {'error': u'данных больше 300 МБ — выгрузите с сервера напрямую'},
                            status=413))
    return _archive(buffer, 'marathon-data-%s.zip' % stamp)


# ------------------------------------------------------------------ выгрузка кода

async def export_project(request):
    if not enabled():
        _guard_off()
    _require(request)
    stamp = time.strftime('%Y%m%d-%H%M')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(config.ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name in SKIP_FILES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, config.ROOT)
                zf.write(full, rel)
                if buffer.tell() > MAX_EXPORT:
                    return _no_store(web.json_response(
                        {'error': u'код больше 300 МБ — что-то лишнее попало в папку'},
                        status=413))
    return _archive(buffer, 'marathon-project-%s.zip' % stamp)


# ------------------------------------------------------------------ полная очистка

async def wipe(request):
    if not enabled():
        _guard_off()
    _require(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    confirm = body.get('confirm', '') if isinstance(body, dict) else ''
    if confirm != CONFIRM_WORD:
        return _no_store(web.json_response(
            {'error': u'для очистки пришлите слово %s в поле confirm' % CONFIRM_WORD},
            status=400))
    было = db.wipe_all()
    files = _clear_media() + _clear_db_copies()
    log.warning(u'панель владельца: данные стёрты, строк было %s, файлов удалено %d',
                было, files)
    return _no_store(web.json_response({'wiped': было, 'media_files': files}))


# ------------------------------------------------------------------ мелочи

def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
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
    u"""Удалить копии базы «перед запуском» (backup.before_start): в них те же
    данные участников, и без этого «полная очистка» оставила бы их на диске."""
    removed = 0
    base = db.path()
    if not base:
        return 0
    for extra in glob.glob(glob.escape(base) + '.before-*'):
        try:
            os.remove(extra)
            removed += 1
        except OSError:
            pass
    return removed


def _archive(buffer: io.BytesIO, filename: str):
    body = buffer.getvalue()
    response = web.Response(body=body, headers={
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="%s"' % filename,
    })
    return _no_store(response)


def register(app: web.Application) -> None:
    u"""Подключить маршруты панели к серверу пульта."""
    app.router.add_get('/owner', page)
    app.router.add_post('/owner/api/login', login)
    app.router.add_post('/owner/api/logout', logout)
    app.router.add_get('/owner/api/overview', overview)
    app.router.add_get('/owner/api/export/data', export_data)
    app.router.add_get('/owner/api/export/project', export_project)
    app.router.add_post('/owner/api/wipe', wipe)
