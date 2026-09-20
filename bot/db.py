# -*- coding: utf-8 -*-
u"""Хранилище: sqlite без ORM.

Обычный sqlite3, а не асинхронный драйвер: все запросы здесь — точечные
чтения и вставки по первичному ключу, они укладываются в доли миллисекунды
и цикл событий не держат. Зато нет лишней зависимости и всё видно глазами.

Главная таблица — jobs, очередь отложенных шагов. Она и делает воронку
переживающей перезапуск: паузы тут по два с половиной часа, и держать их
в памяти нельзя — любой рестарт бота обрубил бы людей на середине.
"""
import os
import sqlite3
import time

SCHEMA = u'''
CREATE TABLE IF NOT EXISTS users (
  user_id     INTEGER PRIMARY KEY,
  username    TEXT,
  first_name  TEXT,
  started_at  REAL NOT NULL,
  launched_at REAL,
  source      TEXT,          -- откуда пришёл: хвост ссылки t.me/бот?start=…
  poll        TEXT,          -- какой опросник сейчас ждёт ответа
  care_open   INTEGER DEFAULT 0,
  -- Множитель пауз. Единица — боевые сроки заказчика. /test ставит
  -- маленький, чтобы прогнать все четыре дня за несколько минут:
  -- принимать воронку, ожидая по два с половиной часа, невозможно.
  speed       REAL DEFAULT 1.0
);

-- Очередь. UNIQUE не даёт задвоить шаг, если пользователь нажал кнопку
-- дважды или планировщик подхватил задачу на границе тика.
CREATE TABLE IF NOT EXISTS jobs (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  chain   TEXT NOT NULL,
  pos     INTEGER NOT NULL,
  run_at  REAL NOT NULL,
  tries   INTEGER NOT NULL DEFAULT 0,
  UNIQUE(user_id, chain, pos)
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(run_at);

CREATE TABLE IF NOT EXISTS answers (
  user_id   INTEGER NOT NULL,
  poll      TEXT NOT NULL,
  answer    TEXT NOT NULL,
  answered  REAL NOT NULL,
  PRIMARY KEY (user_id, poll)
);

CREATE TABLE IF NOT EXISTS purchases (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  product TEXT NOT NULL,
  at      REAL NOT NULL
);

-- Записи дней, отзывы-картинки и file_id уже отправленных кружков.
CREATE TABLE IF NOT EXISTS content (
  key   TEXT PRIMARY KEY,
  kind  TEXT NOT NULL,       -- video | photo | link | circle
  value TEXT NOT NULL,
  at    REAL NOT NULL
);

-- Мост службы заботы: по какому сообщению в чате поддержки кому отвечать.
CREATE TABLE IF NOT EXISTS care_links (
  chat_id    INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  user_id    INTEGER NOT NULL,
  PRIMARY KEY (chat_id, message_id)
);

-- Кому день ушёл без записи (записи ещё не было). Отсюда /resend знает,
-- кому дослать, и не шлёт запись второй раз тем, кто её получил.
CREATE TABLE IF NOT EXISTS missed (
  user_id INTEGER NOT NULL,
  day     INTEGER NOT NULL,
  at      REAL NOT NULL,
  PRIMARY KEY (user_id, day)
);

-- Что человек получил: каждый выполненный шаг воронки — день, вопрос,
-- кнопки покупки (с 11.09.2026). Без этого бот не знал, кто докуда дошёл,
-- и подробной статистики было не из чего строить.
CREATE TABLE IF NOT EXISTS events (
  user_id INTEGER NOT NULL,
  kind    TEXT NOT NULL,      -- day | poll | offer
  ref     TEXT NOT NULL DEFAULT '',
  at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id);

-- Переписка команды с человеком: что он написал боту и что мы ответили.
-- До 18.09.2026 переписка нигде не хранилась — сообщения просто
-- пересылались команде в личку и терялись в ленте уведомлений. Пульту
-- админа (bot/web.py) нужна история: открыл человека — видишь весь диалог.
CREATE TABLE IF NOT EXISTS messages (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,      -- с кем переписка
  side    TEXT NOT NULL,         -- in — от человека, out — от команды
  author  INTEGER,               -- кто из команды отправил (для out)
  kind    TEXT NOT NULL,         -- text | photo | video | voice | video_note | document | …
  text    TEXT,                  -- текст или подпись под медиа
  file_id TEXT,                  -- медиа, если было
  at      REAL NOT NULL,
  tg_id   INTEGER,               -- номер сообщения в Telegram: по нему правим и удаляем
  gone    INTEGER NOT NULL DEFAULT 0   -- удалено из переписки
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, at);

-- Рассылка «уведомить всех» (AleX 18.09.2026). Храним не текст, а ссылку
-- на исходное сообщение: бот копирует его каждому, и медиа со ссылками
-- уходят как есть. cursor — докуда дошли: рассылка переживает перезапуск
-- и не начинается заново, иначе люди получили бы её дважды.
CREATE TABLE IF NOT EXISTS broadcasts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id    INTEGER NOT NULL,     -- где лежит исходное сообщение
  message_id INTEGER NOT NULL,
  author     INTEGER NOT NULL,     -- кто из команды запустил
  at         REAL NOT NULL,
  cursor     INTEGER NOT NULL DEFAULT 0,   -- последний, кому отправили
  sent       INTEGER NOT NULL DEFAULT 0,
  gone       INTEGER NOT NULL DEFAULT 0,   -- закрыли бота
  failed     INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'ready', -- ready | going | done | cancelled
  targets    TEXT                          -- кому именно; пусто — всем
);

-- Эфиры (Павел 20.09.2026). Бот не ведёт трансляцию — он собирает на неё
-- людей: объявляет, напоминает за час и за десять минут и зовёт в момент
-- начала. Напоминания живут в базе, иначе деплой между анонсом и эфиром
-- стёр бы расписание.
CREATE TABLE IF NOT EXISTS lives (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  url    TEXT NOT NULL,          -- куда вести людей
  text   TEXT,                   -- свои слова к анонсу
  at     REAL NOT NULL,          -- когда начинается
  author INTEGER NOT NULL,
  made   REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready'   -- ready | going | done | cancelled
);

-- Какие напоминания уже ушли: одно на эфир и срок, дважды не шлём.
CREATE TABLE IF NOT EXISTS live_reminders (
  live_id INTEGER NOT NULL,
  left    INTEGER NOT NULL,      -- за сколько секунд до начала
  at      REAL NOT NULL,
  PRIMARY KEY (live_id, left)
);

-- Чёрный список (AleX 16.09.2026): одна строка на человека. Убрали из
-- списка — строка остаётся с removed_at: статистике нужна история, а не
-- только кто в списке сейчас. Имя и ник — на момент добавления: человек
-- мог ни разу не писать боту, а банят его и в чатах Павла.
CREATE TABLE IF NOT EXISTS blacklist (
  user_id    INTEGER PRIMARY KEY,
  username   TEXT,
  first_name TEXT,
  added_at   REAL NOT NULL,
  added_by   TEXT,
  removed_at REAL,
  removed_by TEXT,
  chats_note TEXT             -- чем кончились баны в чатах Павла
);

-- Каналы и чаты, где бот — админ: там чёрный список банит. Бот узнаёт о
-- них сам, когда его назначают админом (handlers/blacklist.py).
CREATE TABLE IF NOT EXISTS chats (
  chat_id      INTEGER PRIMARY KEY,
  title        TEXT,
  kind         TEXT,
  can_restrict INTEGER NOT NULL DEFAULT 0,
  updated_at   REAL NOT NULL,
  left_at      REAL
);
'''

# Человек сейчас в чёрном списке — условие для запросов по users (алиас u).
ACTIVE_BAN = ('EXISTS (SELECT 1 FROM blacklist b WHERE b.user_id = u.user_id '
              'AND b.removed_at IS NULL)')

_conn = None


def connect(path: str) -> sqlite3.Connection:
    u"""Открыть базу и создать таблицы. Вызывается один раз на старте."""
    global _conn
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    # WAL: чтение не блокирует запись, а на нежданном выключении питания
    # база остаётся целой — бот стоит на сервере без ИБП.
    _conn.execute('PRAGMA journal_mode=WAL')
    # Свой lower: встроенный в sqlite знает только латиницу, и поиск по
    # пульту «ната» не находил Наталью. Питон умеет любой алфавит.
    _conn.create_function('rulower', 1, lambda s: s.lower() if s else s)
    _conn.executescript(SCHEMA)
    # База, созданная до 07.09.2026, колонки source не знает — добавляем.
    columns = [row[1] for row in _conn.execute('PRAGMA table_info(users)')]
    if 'source' not in columns:
        _conn.execute('ALTER TABLE users ADD COLUMN source TEXT')
    # 11.09.2026: когда человек закрыл бота — для статистики «ушли».
    if 'blocked_at' not in columns:
        _conn.execute('ALTER TABLE users ADD COLUMN blocked_at REAL')
    # 12.09.2026: номер заявки с сайта (сверка с заявками сайта) и счётчик
    # запусков марафона (уведомление о входе: «в который раз»).
    if 'lead_no' not in columns:
        _conn.execute('ALTER TABLE users ADD COLUMN lead_no INTEGER')
    if 'launches' not in columns:
        _conn.execute('ALTER TABLE users ADD COLUMN launches INTEGER NOT NULL DEFAULT 0')
        _conn.execute('UPDATE users SET launches=1 WHERE launched_at IS NOT NULL')
    # 15.09.2026: когда ушёл дожим «заходи на марафон» (bot/nudge.py)
    if 'nudged_at' not in columns:
        _conn.execute('ALTER TABLE users ADD COLUMN nudged_at REAL')
    # 19.09.2026: править и удалять отправленное из пульта — для этого
    # нужен номер сообщения в самом Telegram.
    message_columns = [row[1] for row in _conn.execute('PRAGMA table_info(messages)')]
    if message_columns and 'tg_id' not in message_columns:
        _conn.execute('ALTER TABLE messages ADD COLUMN tg_id INTEGER')
    if message_columns and 'gone' not in message_columns:
        _conn.execute('ALTER TABLE messages ADD COLUMN gone INTEGER NOT NULL DEFAULT 0')
    # 19.09.2026: рассылка не всем, а выбранным — список получателей.
    broadcast_columns = [row[1] for row in _conn.execute('PRAGMA table_info(broadcasts)')]
    if broadcast_columns and 'targets' not in broadcast_columns:
        _conn.execute('ALTER TABLE broadcasts ADD COLUMN targets TEXT')
    _conn.commit()
    return _conn


def _run(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    cur = _conn.execute(sql, args)
    _conn.commit()
    return cur


# ------------------------------------------------------------------ люди

def remember_user(user_id: int, username: str | None, first_name: str | None,
                  source: str = '') -> None:
    u"""Запомнить человека. Источник — первый непустой: откуда пришёл в
    первый раз, оттуда и пришёл; повторный /start по другой ссылке его
    не переписывает."""
    _run('INSERT INTO users (user_id, username, first_name, started_at, source) '
         'VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET '
         'username=excluded.username, first_name=excluded.first_name, '
         "source=COALESCE(NULLIF(users.source, ''), excluded.source), "
         # написал боту или нажал «Старт» — значит, больше не закрыт
         'blocked_at=NULL',
         (user_id, username, first_name, time.time(), source or ''))


# ------------------------------------------------------------ источники

def new_users(since: float) -> list[tuple[str, int]]:
    u"""Сколько пришло с момента since, по источникам, больше — выше."""
    rows = _conn.execute(
        "SELECT COALESCE(source, '') AS src, COUNT(*) AS n FROM users "
        'WHERE started_at >= ? GROUP BY src ORDER BY n DESC, src', (since,)).fetchall()
    return [(r['src'], r['n']) for r in rows]


def source_funnel(since: float) -> list[dict]:
    u"""Что дал каждый источник: пришло → запустили → отвечали → дошли → купили.

    AleX 09.09.2026: «мы разные рассылки используем в телеграме, нужно
    понять, какая из них лучше и продуктивнее». По одному числу «пришло»
    этого не увидеть: рассылка может привести сотню зевак и ни одного
    человека, который дойдёт до конца. Поэтому считаем весь путь.

    «Дошли» — ответившие на опросник третьего дня: он приходит перед
    четвёртым днём, и дальше остаются только кнопки покупки.
    """
    rows = _conn.execute(
        "SELECT COALESCE(u.source, '') AS src,"
        ' COUNT(*) AS people,'
        ' SUM(CASE WHEN u.launched_at IS NOT NULL THEN 1 ELSE 0 END) AS launched,'
        ' SUM(CASE WHEN EXISTS (SELECT 1 FROM answers a WHERE a.user_id = u.user_id)'
        '     THEN 1 ELSE 0 END) AS active,'
        " SUM(CASE WHEN EXISTS (SELECT 1 FROM answers a WHERE a.user_id = u.user_id"
        "     AND a.poll = 'day3') THEN 1 ELSE 0 END) AS finished,"
        ' SUM(CASE WHEN EXISTS (SELECT 1 FROM purchases p WHERE p.user_id = u.user_id)'
        '     THEN 1 ELSE 0 END) AS buys'
        ' FROM users u WHERE u.started_at >= ?'
        ' GROUP BY src ORDER BY people DESC, src', (since,)).fetchall()
    return [dict(r) for r in rows]


def recent_users(limit: int = 30, since: float = 0) -> list[dict]:
    u"""Кто заходил в бота — свежие сверху. Для /кто."""
    rows = _conn.execute(
        'SELECT user_id, username, first_name, started_at, launched_at,'
        " COALESCE(source, '') AS source, lead_no, launches FROM users WHERE started_at >= ?"
        ' ORDER BY started_at DESC LIMIT ?', (since, limit)).fetchall()
    return [dict(r) for r in rows]


def launched_since(since: float) -> int:
    return _conn.execute('SELECT COUNT(*) FROM users WHERE launched_at >= ?',
                         (since,)).fetchone()[0]


def get_user(user_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()
    return dict(row) if row else None


def find_by_username(username: str) -> dict | None:
    u"""Человек по нику без @, без учёта регистра. Ник в Telegram можно
    сменить — в базе последний, под которым человек заходил в бота."""
    row = _conn.execute('SELECT * FROM users WHERE lower(username)=lower(?) '
                        'ORDER BY started_at DESC LIMIT 1', (username,)).fetchone()
    return dict(row) if row else None


def mark_launched(user_id: int) -> bool:
    u"""Отметить запуск. False — если человек уже запускал воронку раньше."""
    cur = _run('UPDATE users SET launched_at=? WHERE user_id=? AND launched_at IS NULL',
               (time.time(), user_id))
    return cur.rowcount > 0


def count_launch(user_id: int) -> int:
    u"""Отметить ещё один запуск марафона; вернуть, который он по счёту."""
    _run('UPDATE users SET launches=launches+1 WHERE user_id=?', (user_id,))
    row = _conn.execute('SELECT launches FROM users WHERE user_id=?', (user_id,)).fetchone()
    return int(row[0]) if row else 1


def set_lead_no(user_id: int, number: int) -> None:
    u"""Номер заявки с сайта — первый, по которому человек пришёл."""
    _run('UPDATE users SET lead_no=? WHERE user_id=? AND lead_no IS NULL', (number, user_id))


def waiting_leads() -> list[dict]:
    u"""Пришли по заявке с сайта, марафон не запускали, бота не закрывали."""
    rows = _conn.execute(
        "SELECT user_id, username, first_name, started_at, lead_no FROM users u "
        "WHERE source = 'zayavka' AND launched_at IS NULL AND blocked_at IS NULL "
        "AND NOT " + ACTIVE_BAN + " ORDER BY started_at").fetchall()
    return [dict(r) for r in rows]


def set_poll(user_id: int, poll: str | None) -> None:
    _run('UPDATE users SET poll=? WHERE user_id=?', (poll, user_id))


def set_speed(user_id: int, speed: float) -> None:
    _run('UPDATE users SET speed=? WHERE user_id=?', (speed, user_id))


def get_speed(user_id: int) -> float:
    row = _conn.execute('SELECT speed FROM users WHERE user_id=?', (user_id,)).fetchone()
    return float(row['speed']) if row and row['speed'] else 1.0


def reset_funnel(user_id: int) -> None:
    u"""Убрать все шаги и ответы человека — для повторного прогона."""
    _run('DELETE FROM jobs WHERE user_id=?', (user_id,))
    _run('DELETE FROM answers WHERE user_id=?', (user_id,))
    # и записанные шаги прошлого прогона: иначе статистика считала бы
    # прошлые вопросы безответными и мерила время ответа через прогоны
    _run('DELETE FROM events WHERE user_id=?', (user_id,))
    _run('UPDATE users SET launched_at=NULL, poll=NULL, nudged_at=NULL WHERE user_id=?', (user_id,))


def nudge_candidates(since: float, launched_before: float) -> list[int]:
    u"""Кому пора дожим: запустил марафон между since и launched_before,
    первый день получил, на вопрос после него не ответил, бота не закрывал,
    дожима ещё не было (bot/nudge.py)."""
    rows = _conn.execute(
        "SELECT u.user_id FROM users u WHERE u.launched_at IS NOT NULL "
        "AND u.launched_at >= ? AND u.launched_at <= ? "
        "AND u.nudged_at IS NULL AND u.blocked_at IS NULL AND NOT " + ACTIVE_BAN + " "
        "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.user_id = u.user_id AND a.poll = 'day1') "
        "AND EXISTS (SELECT 1 FROM events e WHERE e.user_id = u.user_id "
        "            AND e.kind = 'day' AND e.ref = '1') "
        "ORDER BY u.launched_at", (since, launched_before)).fetchall()
    return [r[0] for r in rows]


def mark_nudged(user_id: int) -> None:
    _run('UPDATE users SET nudged_at=? WHERE user_id=?', (time.time(), user_id))


def poll_nudge_candidates(poll: str, since: float, asked_before: float) -> list[int]:
    u"""Кому пора дожим после второго или третьего дня: вопрос ему ушёл между
    since и asked_before, ответа на него нет, дожима по этому вопросу ещё не
    было, бота не закрывал (bot/nudge.py).

    Считаем от первой отправки вопроса: человек мог вернуться и получить его
    заново, но часы молчания идут с того раза, когда его спросили впервые.
    Отметка о дожиме — тоже шаг (events, kind='nudge'), и «пройти заново»
    стирает её вместе с остальными шагами прогона.

    u.poll = ? — человек и правда стоит сейчас на этом вопросе. Пока
    автопереход выключен (POLL_FALLBACK_HOURS = 0), это то же самое, что
    «не ответил»; включат обратно — дожим не уйдёт тому, кого воронка уже
    увела дальше сама.
    """
    rows = _conn.execute(
        'SELECT u.user_id, MIN(e.at) AS asked FROM users u '
        "JOIN events e ON e.user_id = u.user_id AND e.kind = 'poll' AND e.ref = ? "
        'WHERE u.poll = ? AND u.blocked_at IS NULL AND NOT ' + ACTIVE_BAN + ' '
        'AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.user_id = u.user_id AND a.poll = ?) '
        'AND NOT EXISTS (SELECT 1 FROM events n WHERE n.user_id = u.user_id '
        "                AND n.kind = 'nudge' AND n.ref = ?) "
        'GROUP BY u.user_id HAVING asked >= ? AND asked <= ? '
        'ORDER BY asked', (poll, poll, poll, poll, since, asked_before)).fetchall()
    return [r[0] for r in rows]


def mark_poll_nudged(user_id: int, poll: str) -> None:
    log_event(user_id, 'nudge', poll)


def set_care_open(user_id: int, is_open: bool) -> None:
    _run('UPDATE users SET care_open=? WHERE user_id=?', (1 if is_open else 0, user_id))


def save_answer(user_id: int, poll: str, answer: str) -> None:
    _run('INSERT INTO answers (user_id, poll, answer, answered) VALUES (?, ?, ?, ?) '
         'ON CONFLICT(user_id, poll) DO UPDATE SET answer=excluded.answer, '
         'answered=excluded.answered', (user_id, poll, answer, time.time()))


def answered(user_id: int, poll: str) -> bool:
    u"""Ответил ли человек на вопрос в этом прогоне (reset_funnel стирает ответы)."""
    return _conn.execute('SELECT 1 FROM answers WHERE user_id=? AND poll=?',
                         (user_id, poll)).fetchone() is not None


# ------------------------------------------------------------------ эфир

def live_add(url: str, text: str, at: float, author: int) -> int:
    return _run('INSERT INTO lives (url, text, at, author, made) VALUES (?, ?, ?, ?, ?)',
                (url, text or '', at, author, time.time())).lastrowid


def live(live_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM lives WHERE id=?', (live_id,)).fetchone()
    return dict(row) if row else None


def live_status(live_id: int, status: str) -> None:
    _run('UPDATE lives SET status=? WHERE id=?', (status, live_id))


def lives_going() -> list[dict]:
    u"""Объявленные эфиры, которые ещё не прошли."""
    rows = _conn.execute("SELECT * FROM lives WHERE status='going' ORDER BY at").fetchall()
    return [dict(r) for r in rows]


def live_next() -> dict | None:
    u"""Ближайший объявленный эфир — для меню и /эфир без аргументов."""
    row = _conn.execute("SELECT * FROM lives WHERE status IN ('ready','going') "
                        'AND at > ? ORDER BY at LIMIT 1', (time.time(),)).fetchone()
    return dict(row) if row else None


def live_remind(live_id: int, left: int) -> None:
    _run('INSERT OR IGNORE INTO live_reminders (live_id, left, at) VALUES (?, ?, ?)',
         (live_id, left, time.time()))


def live_reminded(live_id: int, left: int) -> bool:
    return _conn.execute('SELECT 1 FROM live_reminders WHERE live_id=? AND left=?',
                         (live_id, left)).fetchone() is not None


# --------------------------------------------------------- отчёт за день

def day_stats(since: float, until: float) -> dict:
    u"""Что случилось в промежутке: пришли, запустили, ответили, купили.

    AleX 18.09.2026: «сколько всего в этот день пришло новых, сколько из
    них нажали Да или Нет после первой практики, сколько после второй,
    третий и четвёртый, и коротко источники против каждого пункта цифра».
    Считаем по времени самого события: ответ засчитывается в те сутки,
    когда человек нажал кнопку, а не когда он пришёл в бота.
    """
    def one(sql: str) -> int:
        return _conn.execute(sql, (since, until)).fetchone()[0]

    answers = _conn.execute(
        'SELECT poll, answer, COUNT(*) AS n FROM answers '
        'WHERE answered >= ? AND answered < ? GROUP BY poll, answer',
        (since, until)).fetchall()
    days = _conn.execute(
        "SELECT ref, COUNT(DISTINCT user_id) AS n FROM events "
        "WHERE kind='day' AND at >= ? AND at < ? GROUP BY ref", (since, until)).fetchall()
    sources = _conn.execute(
        "SELECT COALESCE(source, '') AS src, COUNT(*) AS n FROM users "
        'WHERE started_at >= ? AND started_at < ? GROUP BY src ORDER BY n DESC, src',
        (since, until)).fetchall()
    buys = _conn.execute(
        'SELECT product, COUNT(*) AS n FROM purchases WHERE at >= ? AND at < ? '
        'GROUP BY product', (since, until)).fetchall()
    return {
        'came': one('SELECT COUNT(*) FROM users WHERE started_at >= ? AND started_at < ?'),
        'launched': one('SELECT COUNT(*) FROM users WHERE launched_at >= ? AND launched_at < ?'),
        'answers': {(r['poll'], r['answer']): r['n'] for r in answers},
        'days': {int(r['ref']): r['n'] for r in days if str(r['ref']).isdigit()},
        'sources': [(r['src'], r['n']) for r in sources],
        'buys': {r['product']: r['n'] for r in buys},
        'left': one('SELECT COUNT(*) FROM users WHERE blocked_at >= ? AND blocked_at < ?'),
    }


# ------------------------------------------------------------- рассылка

def broadcast_add(chat_id: int, message_id: int, author: int,
                  targets: list | None = None) -> int:
    u"""Новая рассылка. targets — кому именно; пусто (None) — всем."""
    listed = ','.join(str(int(uid)) for uid in targets) if targets else None
    return _run('INSERT INTO broadcasts (chat_id, message_id, author, at, targets) '
                'VALUES (?, ?, ?, ?, ?)',
                (chat_id, message_id, author, time.time(), listed)).lastrowid


def broadcast_picked(task: dict) -> list:
    u"""Выбранные получатели рассылки; пустой список — рассылка всем."""
    raw = (task or {}).get('targets') or ''
    return [int(piece) for piece in raw.split(',') if piece.strip().lstrip('-').isdigit()]


def broadcast(broadcast_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM broadcasts WHERE id=?', (broadcast_id,)).fetchone()
    return dict(row) if row else None


def broadcast_status(broadcast_id: int, status: str) -> None:
    _run('UPDATE broadcasts SET status=? WHERE id=?', (status, broadcast_id))


def broadcasts_going() -> list[dict]:
    u"""Рассылки, прерванные перезапуском, — их продолжают с курсора."""
    rows = _conn.execute("SELECT * FROM broadcasts WHERE status='going' ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# Кому уходит рассылка: все, кто заходил в бота, кроме закрывших его и тех,
# кто в чёрном списке. Команду проекта тоже не трогаем — они получат отчёт.
_BROADCAST_WHERE = ('WHERE u.user_id > ? AND u.blocked_at IS NULL AND NOT ' + ACTIVE_BAN + ' ')


def broadcast_targets(after: int, limit: int = 200, picked: list | None = None) -> list[int]:
    u"""Следующая порция получателей после того, кому уже отправили."""
    if picked:
        holes = ','.join('?' * len(picked))
        rows = _conn.execute(
            'SELECT u.user_id FROM users u ' + _BROADCAST_WHERE +
            'AND u.user_id IN (' + holes + ') ORDER BY u.user_id LIMIT ?',
            (after,) + tuple(picked) + (limit,)).fetchall()
    else:
        rows = _conn.execute('SELECT u.user_id FROM users u ' + _BROADCAST_WHERE +
                             'ORDER BY u.user_id LIMIT ?', (after, limit)).fetchall()
    return [r[0] for r in rows]


def broadcast_left(after: int = 0, picked: list | None = None) -> int:
    if picked:
        holes = ','.join('?' * len(picked))
        return _conn.execute(
            'SELECT COUNT(*) FROM users u ' + _BROADCAST_WHERE +
            'AND u.user_id IN (' + holes + ')', (after,) + tuple(picked)).fetchone()[0]
    return _conn.execute('SELECT COUNT(*) FROM users u ' + _BROADCAST_WHERE,
                         (after,)).fetchone()[0]


def broadcast_step(broadcast_id: int, user_id: int, result: str) -> dict:
    u"""Отметить одного получателя и подвинуть курсор — чтобы перезапуск не
    заставил слать заново тем, кто уже получил."""
    column = {'ok': 'sent', 'gone': 'gone'}.get(result, 'failed')
    _run('UPDATE broadcasts SET cursor=?, %s=%s+1 WHERE id=?' % (column, column),
         (user_id, broadcast_id))
    return broadcast(broadcast_id)


def timeline(user_id: int) -> list:
    u"""Шаги воронки человека: что бот прислал и что человек ответил.

    AleX 19.09.2026: «в некоторых диалогах непонятно — просто одно слово,
    а на что это был ответ, не видно». Слово «Да» — это ответ на вопрос
    бота, и в переписке он должен стоять рядом с самим вопросом.
    """
    rows = _conn.execute(
        "SELECT kind, ref, at FROM events WHERE user_id=? "
        "AND kind IN ('day', 'poll', 'offer') ORDER BY at", (user_id,)).fetchall()
    out = [{'kind': r['kind'], 'ref': str(r['ref'] or ''), 'at': r['at']} for r in rows]
    answers = _conn.execute(
        'SELECT poll, answer, answered FROM answers WHERE user_id=?', (user_id,)).fetchall()
    out += [{'kind': 'answer', 'ref': r['poll'], 'answer': r['answer'], 'at': r['answered']}
            for r in answers]
    return sorted(out, key=lambda item: item['at'])


def stuck_on_poll() -> list[dict]:
    u"""Кто стоит на вопросе без ответа и ждёт кнопок.

    До 20.09.2026 кнопки «Да»/«Нет» приходили отдельным сообщением через
    два с половиной часа, а в тексте дня уже было написано «нажми на
    кнопках ниже». Люди искали их под записью и не находили — эти и
    застряли. Берём тех, у кого вопрос открыт, ответа нет, бота не
    закрывали и в чёрном списке не состоят.
    """
    rows = _conn.execute(
        'SELECT u.user_id, u.poll FROM users u '
        "WHERE u.poll IS NOT NULL AND u.poll != '' "
        'AND u.blocked_at IS NULL AND NOT ' + ACTIVE_BAN + ' '
        'AND NOT EXISTS (SELECT 1 FROM answers a '
        '                WHERE a.user_id = u.user_id AND a.poll = u.poll) '
        'ORDER BY u.user_id').fetchall()
    return [dict(r) for r in rows]


def answers_of(user_id: int) -> dict:
    u"""Все ответы человека: {'day1': ('yes', когда)} — для карточки в пульте."""
    rows = _conn.execute('SELECT poll, answer, answered FROM answers WHERE user_id=?',
                         (user_id,)).fetchall()
    return {r['poll']: (r['answer'], r['answered']) for r in rows}


def days_of(user_id: int) -> dict:
    u"""Какие дни человек получил и когда: {1: время} (AleX 18.09.2026 —
    «проваливаясь в карточку, видно: что он ответил 1д/2д/3д/4д»)."""
    rows = _conn.execute("SELECT ref, MIN(at) AS at FROM events "
                         "WHERE user_id=? AND kind='day' GROUP BY ref", (user_id,)).fetchall()
    return {int(r['ref']): r['at'] for r in rows if str(r['ref']).isdigit()}


# --------------------------------------------------------------- очередь

def add_job(user_id: int, chain: str, pos: int, run_at: float) -> None:
    _run('INSERT OR IGNORE INTO jobs (user_id, chain, pos, run_at) VALUES (?, ?, ?, ?)',
         (user_id, chain, pos, run_at))


def due_jobs(limit: int = 50) -> list[dict]:
    rows = _conn.execute('SELECT * FROM jobs WHERE run_at<=? ORDER BY run_at LIMIT ?',
                         (time.time(), limit)).fetchall()
    return [dict(r) for r in rows]


def retry_job(job_id: int, run_at: float) -> None:
    u"""Отложить шаг после сбоя и посчитать попытку."""
    _run('UPDATE jobs SET run_at=?, tries=tries+1 WHERE id=?', (run_at, job_id))


def drop_job(job_id: int) -> None:
    _run('DELETE FROM jobs WHERE id=?', (job_id,))


def drop_chains(user_id: int, chains: tuple[str, ...]) -> None:
    u"""Снять запланированные шаги перечисленных цепочек.

    Нужно на ответе в опроснике: там ждёт отложенное добивание по ветке
    «нет», и без снятия человек получил бы обе ветки.
    """
    if not chains:
        return
    marks = ','.join('?' * len(chains))
    _run('DELETE FROM jobs WHERE user_id=? AND chain IN (%s)' % marks,
         (user_id,) + tuple(chains))


def user_jobs(user_id: int) -> list[dict]:
    rows = _conn.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY run_at',
                         (user_id,)).fetchall()
    return [dict(r) for r in rows]


def pending_chains(user_id: int) -> set[str]:
    u"""Цепочки, в которых у человека ещё есть шаги в очереди."""
    rows = _conn.execute('SELECT DISTINCT chain FROM jobs WHERE user_id=?',
                         (user_id,)).fetchall()
    return {r['chain'] for r in rows}


def launched_users() -> list[int]:
    rows = _conn.execute('SELECT user_id FROM users WHERE launched_at IS NOT NULL '
                         'ORDER BY user_id').fetchall()
    return [r['user_id'] for r in rows]


# ------------------------------------------------- дни, ушедшие без записи

def mark_missed(user_id: int, day: int) -> None:
    _run('INSERT OR IGNORE INTO missed (user_id, day, at) VALUES (?, ?, ?)',
         (user_id, day, time.time()))


def clear_missed(user_id: int, day: int) -> None:
    _run('DELETE FROM missed WHERE user_id=? AND day=?', (user_id, day))


def missed_users(day: int) -> list[int]:
    rows = _conn.execute('SELECT user_id FROM missed WHERE day=? ORDER BY at',
                         (day,)).fetchall()
    return [r['user_id'] for r in rows]


# --------------------------------------------------------------- контент

def put_content(key: str, kind: str, value: str) -> None:
    _run('INSERT INTO content (key, kind, value, at) VALUES (?, ?, ?, ?) '
         'ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, value=excluded.value, '
         'at=excluded.at', (key, kind, value, time.time()))


def get_content(key: str) -> tuple[str, str] | None:
    row = _conn.execute('SELECT kind, value FROM content WHERE key=?', (key,)).fetchone()
    return (row['kind'], row['value']) if row else None


# --------------------------------------------------------------- покупки

def add_purchase(user_id: int, product: str) -> int:
    return _run('INSERT INTO purchases (user_id, product, at) VALUES (?, ?, ?)',
                (user_id, product, time.time())).lastrowid


def purchase_presses(user_id: int, product: str) -> list[dict]:
    u"""Все нажатия человека на эту кнопку покупки, старые первыми."""
    rows = _conn.execute('SELECT id, at FROM purchases WHERE user_id=? AND product=? '
                         'ORDER BY id', (user_id, product)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------- служба заботы

def link_care(chat_id: int, message_id: int, user_id: int) -> None:
    _run('INSERT OR REPLACE INTO care_links (chat_id, message_id, user_id) '
         'VALUES (?, ?, ?)', (chat_id, message_id, user_id))


def care_target(chat_id: int, message_id: int) -> int | None:
    row = _conn.execute('SELECT user_id FROM care_links WHERE chat_id=? AND message_id=?',
                        (chat_id, message_id)).fetchone()
    return row['user_id'] if row else None


# ------------------------------------------------------------- переписка

def save_message(user_id: int, side: str, kind: str = 'text', text: str | None = None,
                 file_id: str | None = None, author: int | None = None,
                 tg_id: int | None = None) -> int:
    u"""Записать сообщение переписки: side — 'in' от человека, 'out' от команды.

    tg_id — номер сообщения в самом Telegram: без него отправленное нельзя
    ни поправить, ни удалить у человека (AleX 19.09.2026).
    """
    return _run('INSERT INTO messages (user_id, side, author, kind, text, file_id, at, tg_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (user_id, side, author, kind, text, file_id, time.time(), tg_id)).lastrowid


def message(message_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
    return dict(row) if row else None


def edit_message(message_id: int, text: str) -> None:
    _run('UPDATE messages SET text=? WHERE id=?', (text, message_id))


def drop_message(message_id: int) -> None:
    u"""Пометить удалённым. Строку не стираем: пусть видно, что было."""
    _run('UPDATE messages SET gone=1 WHERE id=?', (message_id,))


def chat_history(user_id: int, limit: int = 200) -> list[dict]:
    u"""Переписка с человеком, старые сверху — как в обычном чате."""
    rows = _conn.execute(
        'SELECT id, side, author, kind, text, file_id, at, tg_id FROM messages '
        'WHERE user_id=? AND gone=0 ORDER BY at DESC, id DESC LIMIT ?',
        (user_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


# Последнее сообщение переписки и сколько входящих ждут ответа: входящими
# считаем те, что пришли после нашего последнего ответа.
_LAST_MESSAGE = (
    'SELECT id FROM messages x WHERE x.user_id = u.user_id AND x.gone = 0 '
    'ORDER BY x.at DESC, x.id DESC LIMIT 1')
_WAITING = (
    "SELECT COUNT(*) FROM messages i WHERE i.user_id = u.user_id AND i.side = 'in' "
    'AND i.gone = 0 '
    "AND i.at > COALESCE((SELECT MAX(o.at) FROM messages o "
    "                     WHERE o.user_id = u.user_id AND o.side = 'out'), 0)")


def people(query: str = '', limit: int = 60, only_chats: bool = False) -> list[dict]:
    u"""Люди для пульта админа: свежая переписка сверху.

    Пустой поиск — те, с кем переписка уже есть (кому отвечать), иначе
    последние пришедшие. Поиск ищет по всей базе: по нику, по имени и по
    id, кусочком и без учёта регистра, — AleX 16.09.2026 просил не искать
    id руками.
    """
    like = u'%%%s%%' % query.strip().lstrip('@').lower()
    where = []
    args: list = []
    if query.strip():
        where.append('(rulower(COALESCE(u.username, ' "''" ')) LIKE ? '
                     'OR rulower(COALESCE(u.first_name, ' "''" ')) LIKE ? '
                     'OR CAST(u.user_id AS TEXT) LIKE ?)')
        args += [like, like, like]
    elif only_chats:
        where.append('m.id IS NOT NULL')
    rows = _conn.execute(
        'SELECT u.user_id, u.username, u.first_name, u.started_at, u.launched_at, '
        "       u.poll, u.blocked_at, COALESCE(u.source, '') AS source, "
        '       m.at AS last_at, m.side AS last_side, m.kind AS last_kind, m.text AS last_text, '
        '       (' + _WAITING + ') AS waiting, '
        '       (SELECT COUNT(*) FROM blacklist b WHERE b.user_id = u.user_id '
        '        AND b.removed_at IS NULL) AS banned '
        'FROM users u LEFT JOIN messages m ON m.id = (' + _LAST_MESSAGE + ') '
        + ('WHERE ' + ' AND '.join(where) + ' ' if where else '') +
        # Второй ключ — номер сообщения: два сообщения могут лечь в одну и ту
        # же долю секунды, и без него порядок людей в списке плавал бы.
        'ORDER BY COALESCE(m.at, u.started_at) DESC, m.id DESC, u.user_id DESC '
        'LIMIT ?', tuple(args) + (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------- шаги и уходы

def log_event(user_id: int, kind: str, ref=None) -> None:
    u"""Записать выполненный шаг воронки: день, вопрос, кнопки покупки."""
    _run('INSERT INTO events (user_id, kind, ref, at) VALUES (?, ?, ?, ?)',
         (user_id, kind, '' if ref is None else str(ref), time.time()))


def mark_blocked(user_id: int) -> None:
    u"""Человек закрыл бота — отметить момент (первый, повторы не трогаем)."""
    _run('UPDATE users SET blocked_at=? WHERE user_id=? AND blocked_at IS NULL',
         (time.time(), user_id))


# ------------------------------------------------------- чёрный список

def is_banned(user_id: int) -> bool:
    u"""Сейчас в чёрном списке. Спрашивается на каждое сообщение — поиск по ключу."""
    return _conn.execute('SELECT 1 FROM blacklist WHERE user_id=? AND removed_at IS NULL',
                         (user_id,)).fetchone() is not None


def ban_entry(user_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM blacklist WHERE user_id=?', (user_id,)).fetchone()
    return dict(row) if row else None


def ban_add(user_id: int, username: str | None, first_name: str | None, by: str) -> bool:
    u"""Внести в список. False — уже там (ничего не меняется)."""
    if is_banned(user_id):
        return False
    _run('INSERT INTO blacklist (user_id, username, first_name, added_at, added_by) '
         'VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET '
         'username=COALESCE(excluded.username, blacklist.username), '
         'first_name=COALESCE(excluded.first_name, blacklist.first_name), '
         'added_at=excluded.added_at, added_by=excluded.added_by, '
         'removed_at=NULL, removed_by=NULL, chats_note=NULL',
         (user_id, username, first_name, time.time(), by))
    return True


def ban_remove(user_id: int, by: str) -> bool:
    u"""Убрать из списка. False — его там и не было."""
    cur = _run('UPDATE blacklist SET removed_at=?, removed_by=? '
               'WHERE user_id=? AND removed_at IS NULL', (time.time(), by, user_id))
    return cur.rowcount > 0


def ban_note(user_id: int, note: str) -> None:
    _run('UPDATE blacklist SET chats_note=? WHERE user_id=?', (note, user_id))


def blacklist() -> list[dict]:
    u"""Весь список с историей: сейчас в списке — сверху, свежие — выше."""
    rows = _conn.execute('SELECT * FROM blacklist ORDER BY removed_at IS NOT NULL, '
                         'COALESCE(removed_at, added_at) DESC').fetchall()
    return [dict(r) for r in rows]


def stop_funnel(user_id: int) -> None:
    u"""Снять человеку все шаги марафона. Ответы и шаги прошлого — остаются:
    это статистика, а не очередь."""
    _run('DELETE FROM jobs WHERE user_id=?', (user_id,))
    _run('UPDATE users SET poll=NULL WHERE user_id=?', (user_id,))


def chat_seen(chat_id: int, title: str | None, kind: str, can_restrict: bool) -> None:
    _run('INSERT INTO chats (chat_id, title, kind, can_restrict, updated_at) '
         'VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET '
         'title=excluded.title, kind=excluded.kind, can_restrict=excluded.can_restrict, '
         'updated_at=excluded.updated_at, left_at=NULL',
         (chat_id, title, kind, 1 if can_restrict else 0, time.time()))


def chat_left(chat_id: int) -> None:
    _run('UPDATE chats SET left_at=?, can_restrict=0 WHERE chat_id=?', (time.time(), chat_id))


def ban_chats() -> list[dict]:
    u"""Где бот сейчас админ с правом блокировать — там и банит."""
    rows = _conn.execute('SELECT * FROM chats WHERE left_at IS NULL AND can_restrict=1 '
                         'ORDER BY title').fetchall()
    return [dict(r) for r in rows]


def known_chats() -> list[dict]:
    u"""Все чаты, где бот сейчас есть, — и с правами, и без."""
    rows = _conn.execute('SELECT * FROM chats WHERE left_at IS NULL ORDER BY title').fetchall()
    return [dict(r) for r in rows]


def unblock(user_id: int) -> None:
    u"""Бот у человека снова открыт: дошло сообщение или он нажал кнопку."""
    _run('UPDATE users SET blocked_at=NULL WHERE user_id=? AND blocked_at IS NOT NULL',
         (user_id,))


def clear_stale_polls() -> int:
    u"""Закрыть вопросы, по которым бот уже сам повёл человека дальше.

    Пока вопрос ждёт ответа, в очереди стоит ветка «нет» (dayN_no) — через
    POLL_FALLBACK_HOURS она пойдёт сама. Её нет — значит, прошла, а вопрос
    остался открытым: до 11.09.2026 его на таймере не закрывали, и старая
    кнопка под ним запускала ветку повторно. Закрывших бота не трогаем:
    вернутся — кнопка под вопросом продолжит марафон. Вызывать, только
    когда добивание включено (POLL_FALLBACK_HOURS > 0).
    """
    cur = _run("UPDATE users SET poll=NULL WHERE poll IS NOT NULL AND blocked_at IS NULL "
               "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.user_id = users.user_id "
               "AND jobs.chain = users.poll || '_no')")
    return cur.rowcount


def drop_poll_fallbacks() -> int:
    u"""Снять отложенные ветки «нет» по вопросам, которые ещё ждут ответа.

    Павел 17.09.2026: без ответа следующий день не приходит. У тех, кого
    спросили до выкладки, ветка «нет» уже стоит на таймере — её и снимаем.
    Ответивший «нет» сюда не попадает: ответ закрывает вопрос (poll = NULL).
    """
    cur = _run("DELETE FROM jobs WHERE pos = 0 AND EXISTS (SELECT 1 FROM users u "
               "WHERE u.user_id = jobs.user_id AND u.poll IS NOT NULL "
               "AND jobs.chain = u.poll || '_no')")
    return cur.rowcount


def snapshot() -> dict[str, list[dict]]:
    u"""Всё, из чего считается подробная статистика (bot/insights.py).

    Одним чтением, без расчётов: база маленькая (сотни и тысячи людей), а
    считать в Python проще и проверяемее, чем городить это в SQL.
    """
    def rows(sql: str) -> list[dict]:
        return [dict(r) for r in _conn.execute(sql).fetchall()]
    return {
        'users': rows("SELECT user_id, username, first_name, started_at, launched_at, "
                      "COALESCE(source, '') AS source, poll, blocked_at, lead_no, launches FROM users"),
        'events': rows("SELECT user_id, kind, ref, at FROM events "
                       "WHERE kind IN ('day', 'poll', 'offer') ORDER BY at"),
        'answers': rows('SELECT user_id, poll, answer, answered FROM answers'),
        'purchases': rows('SELECT id, user_id, product, at FROM purchases ORDER BY at'),
        'jobs': rows('SELECT user_id, chain, pos, run_at FROM jobs'),
        # Сколько человек написал боту. Раньше считалось по care_links, а
        # это мост для реплаев: туда попадает каждое уведомление команде —
        # о запуске, о заявке, — да ещё по записи на каждого получателя.
        # AleX 18.09.2026: «в отчёте больше сотни писавших, а по факту
        # меньше десятка». Настоящие сообщения людей — в messages.
        'care': rows("SELECT user_id, COUNT(*) AS n FROM messages "
                     "WHERE side='in' GROUP BY user_id"),
        'missed': rows('SELECT user_id, day FROM missed'),
        'blacklist': rows('SELECT user_id, username, first_name, added_at, added_by, '
                          'removed_at, removed_by, chats_note FROM blacklist'),
    }


def stats() -> dict[str, int]:
    u"""Короткая сводка для /status."""
    one = lambda sql: _conn.execute(sql).fetchone()[0]
    return {
        'users': one('SELECT COUNT(*) FROM users'),
        'launched': one('SELECT COUNT(*) FROM users WHERE launched_at IS NOT NULL'),
        'jobs': one('SELECT COUNT(*) FROM jobs'),
        # заявка — человек и продукт: повторные нажатия той же кнопки — одна
        'purchases': one('SELECT COUNT(*) FROM (SELECT DISTINCT user_id, product FROM purchases)'),
        'presses': one('SELECT COUNT(*) FROM purchases'),
    }
