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
'''

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
    _conn.executescript(SCHEMA)
    _conn.commit()
    return _conn


def _run(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    cur = _conn.execute(sql, args)
    _conn.commit()
    return cur


# ------------------------------------------------------------------ люди

def remember_user(user_id: int, username: str | None, first_name: str | None) -> None:
    _run('INSERT INTO users (user_id, username, first_name, started_at) '
         'VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET '
         'username=excluded.username, first_name=excluded.first_name',
         (user_id, username, first_name, time.time()))


def get_user(user_id: int) -> dict | None:
    row = _conn.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()
    return dict(row) if row else None


def mark_launched(user_id: int) -> bool:
    u"""Отметить запуск. False — если человек уже запускал воронку раньше."""
    cur = _run('UPDATE users SET launched_at=? WHERE user_id=? AND launched_at IS NULL',
               (time.time(), user_id))
    return cur.rowcount > 0


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
    _run('UPDATE users SET launched_at=NULL, poll=NULL WHERE user_id=?', (user_id,))


def set_care_open(user_id: int, is_open: bool) -> None:
    _run('UPDATE users SET care_open=? WHERE user_id=?', (1 if is_open else 0, user_id))


def save_answer(user_id: int, poll: str, answer: str) -> None:
    _run('INSERT INTO answers (user_id, poll, answer, answered) VALUES (?, ?, ?, ?) '
         'ON CONFLICT(user_id, poll) DO UPDATE SET answer=excluded.answer, '
         'answered=excluded.answered', (user_id, poll, answer, time.time()))


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


# ---------------------------------------------------------- служба заботы

def link_care(chat_id: int, message_id: int, user_id: int) -> None:
    _run('INSERT OR REPLACE INTO care_links (chat_id, message_id, user_id) '
         'VALUES (?, ?, ?)', (chat_id, message_id, user_id))


def care_target(chat_id: int, message_id: int) -> int | None:
    row = _conn.execute('SELECT user_id FROM care_links WHERE chat_id=? AND message_id=?',
                        (chat_id, message_id)).fetchone()
    return row['user_id'] if row else None


def stats() -> dict[str, int]:
    u"""Короткая сводка для /status."""
    one = lambda sql: _conn.execute(sql).fetchone()[0]
    return {
        'users': one('SELECT COUNT(*) FROM users'),
        'launched': one('SELECT COUNT(*) FROM users WHERE launched_at IS NOT NULL'),
        'jobs': one('SELECT COUNT(*) FROM jobs'),
        'purchases': one('SELECT COUNT(*) FROM purchases'),
    }
